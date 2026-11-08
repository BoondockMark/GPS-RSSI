"""Build project terrain artifacts from downloaded native 1 m USGS tiles.

Requires: numpy rasterio shapely pyproj pillow
Usage: python scripts/build_heightmap.py SOURCE_DIRECTORY OUTPUT_DIRECTORY
SOURCE_DIRECTORY contains sources.json (TNM product records) and their GeoTIFFs.
"""

import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageDraw
from pyproj import Transformer
import rasterio
from rasterio.features import geometry_mask
from rasterio.merge import merge
from rasterio.enums import Resampling
from shapely.geometry import shape, mapping
from shapely.ops import transform


def build(source_dir, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    boundary = Path(__file__).resolve().parents[1] / "src/gps_rssi/assets/la-habra-heights.geojson"
    area = json.loads(boundary.read_text())
    polygon = shape(next(f["geometry"] for f in area["features"] if f["properties"]["kind"] == "buffer"))
    sources = json.loads((source_dir / "sources.json").read_text())
    datasets = [rasterio.open(source_dir / s["downloadURL"].split("/")[-1]) for s in sources]
    try:
        crs = datasets[0].crs
        if not all(d.crs == crs and d.res == (1, 1) for d in datasets):
            raise ValueError("Source tiles must share a CRS and native 1-meter resolution")
        projected = transform(Transformer.from_crs(4326, crs, always_xy=True).transform, polygon)
        west, south, east, north = projected.bounds
        bounds = (math.floor(west), math.floor(south), math.ceil(east), math.ceil(north))
        source_index = np.zeros((bounds[3] - bounds[1], bounds[2] - bounds[0]), dtype=np.uint8)
        def first_valid(merged, new, merged_mask, new_mask, index, roff, coff):
            take = np.broadcast_to(merged_mask & ~new_mask, new.shape)
            np.copyto(merged, new, where=take)
            region = source_index[roff:roff + new.shape[1], coff:coff + new.shape[2]]
            region[take[0]] = index + 1
        raster, affine = merge(datasets, bounds=bounds, res=1, nodata=-9999, dtype="float32", method=first_valid)
        elevations = raster[0]
        valid = np.isfinite(elevations) & (elevations != -9999)
        inside = geometry_mask([mapping(projected)], elevations.shape, affine, invert=True)
        if not np.all(valid):
            raise ValueError(f"Missing coverage: {np.count_nonzero(~valid)} pixels in rectangular extent")
        profile = dict(driver="GTiff", width=elevations.shape[1], height=elevations.shape[0],
                       count=1, dtype="float32", crs=crs, transform=affine, nodata=-9999,
                       tiled=True, blockxsize=256, blockysize=256, compress="deflate", predictor=3)
        for name, pixels in [("terrain_1m.tif", elevations),
                             ("terrain_1m_clipped.tif", np.where(inside, elevations, -9999))]:
            with rasterio.open(output_dir / name, "w", **profile) as dst:
                dst.write(pixels, 1)
                dst.set_band_description(1, "Bare-earth elevation, NAVD88 meters")
                dst.update_tags(AREA_OR_POINT="Area", vertical_datum="NAVD88", units="meters",
                                source="USGS 3DEP; preferred CA_LosAngeles_B23 with older 1m gap fill", buffer_meters="804.672")
                dst.build_overviews([2, 4, 8, 16], Resampling.average)
        lo = float(elevations.min())
        hi = float(elevations.max())
        step = (hi - lo) / 65535
        heights = np.rint((elevations - lo) / step).astype(np.uint16)
        Image.fromarray(heights).save(output_dir / "heightmap_16bit.png")
        Image.fromarray(inside.astype(np.uint8) * 255).save(output_dir / "planning_area_mask.png")
        Image.fromarray(source_index).save(output_dir / "source_index.png")
        # A downsampled shaded-relief preview; full-size data stay in the GeoTIFFs.
        stride = max(1, math.ceil(elevations.shape[1] / 1600))
        small = elevations[::stride, ::stride]
        dy, dx = np.gradient(small, stride, stride)
        norm = np.sqrt(dx * dx + dy * dy + 1)
        light = np.maximum(0, (-dx * -0.5 + dy * 0.5 + 0.7071) / norm)
        t = (small - lo) / (hi - lo)
        low, high = np.array([94, 143, 90]), np.array([228, 214, 177])
        rgb = (low[None, None, :] * (1 - t[..., None]) + high * t[..., None])
        rgb *= (0.45 + 0.65 * light)[..., None]
        rgb[~inside[::stride, ::stride]] *= 0.55
        preview = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
        draw = ImageDraw.Draw(preview)
        for feature, color in [(area["features"][0], "cyan"), (area["features"][1], "white")]:
            outline = transform(Transformer.from_crs(4326, crs, always_xy=True).transform, shape(feature["geometry"]))
            draw.line([((x - bounds[0]) / stride, (bounds[3] - y) / stride) for x, y in outline.exterior.coords], fill=color, width=2)
        draw.rectangle((0, 0, 620, 40), fill="black")
        draw.text((10, 7), "La Habra Heights + 0.5 mile | USGS 1 m bare-earth terrain", fill="white")
        draw.text((10, 23), "North up | white: city | cyan: planning buffer", fill="white")
        preview.save(output_dir / "preview.png")
        shutil.copyfile(boundary, output_dir / "planning_area.geojson")
        metadata = dict(created_utc=datetime.now(timezone.utc).isoformat(),
                        source="USGS 3DEP / CA_LosAngeles_B23, CA_SoCal_Wildfires_B1_2018, CA_LosAngeles_2016", sources=sources,
                        source_pixel_counts=[dict(index=i + 1, title=s['title'], rectangular_pixels=int((source_index == i + 1).sum()), planning_pixels=int(((source_index == i + 1) & inside).sum())) for i, s in enumerate(sources)],
                        horizontal_crs=crs.to_string(), horizontal_wkt=crs.to_wkt(),
                        vertical_datum="NAVD88", elevation_units="meters", pixel_size_meters=1,
                        width=elevations.shape[1], height=elevations.shape[0],
                        bounds_projected=list(bounds), transform=list(affine)[:6],
                        wgs84_planning_bounds=list(polygon.bounds),
                        planning_area_provenance=area["provenance"],
                        valid_planning_pixels=int(inside.sum()), missing_planning_pixels=int((inside & ~valid).sum()),
                        rectangular_elevation_min_m=lo, rectangular_elevation_max_m=hi,
                        planning_elevation_min_m=float(elevations[inside].min()),
                        planning_elevation_max_m=float(elevations[inside].max()),
                        png_decode="elevation_m = pixel_value * png_meters_per_level + png_offset_m",
                        png_offset_m=lo, png_meters_per_level=step,
                        png_orientation="north up; row 0 is north; column 0 is west",
                        boundary_sha256=hashlib.sha256(boundary.read_bytes()).hexdigest())
        (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (output_dir / "README.md").write_text(f"""# La Habra Heights + 0.5-mile height map

Native USGS 3DEP 1-meter lidar-derived bare-earth DEM. Preferred tiles are from
CA_LosAngeles_B23, published August 2025 (publication date is not survey date).
Gaps are filled using CA_SoCal_Wildfires_B1_2018 and CA_LosAngeles_2016 native
1-meter data, in that order. Different survey dates may produce seams; no
smoothing or artificial infill was applied. Source precedence and pixel counts
are recorded in metadata.json; source_index.png records the 1-based source
record used for each output pixel (0 would mean missing coverage).
Public-domain USGS data. Source records and URLs are in metadata.json.

- terrain_1m_clipped.tif: exact project planning polygon; outside is NoData (-9999).
- terrain_1m.tif: complete rectangular terrain extent, suitable for GIS/terrain engines.
- heightmap_16bit.png: unsigned 16-bit grayscale, same rectangular grid as terrain_1m.tif.
- planning_area_mask.png: 255 inside planning polygon, 0 outside; aligned with the PNG.
- source_index.png: source-record index for each pixel, aligned with the PNG.
- planning_area.geojson: existing project city and 0.5-mile buffer polygons (WGS84).
- preview.png: shaded terrain preview, north up; white city outline, cyan planning buffer.
- metadata.json: CRS, bounds, scaling, coverage check and source provenance.

Grid: {elevations.shape[1]} columns x {elevations.shape[0]} rows; 1 x 1 meter cells.
Horizontal reference: {crs.to_string()} (NAD83 / UTM zone 11N).
Vertical reference: NAVD88, meters. This is not WGS84 ellipsoidal/GPS height.
Planning region elevation range: {metadata['planning_elevation_min_m']:.3f} to {metadata['planning_elevation_max_m']:.3f} m.
No missing cells within the planning region or its rectangular extent.

## Importing the height image

Use the 16-bit PNG as linear height data with gamma/color correction disabled.
Pixel value 0 = {lo:.9f} m; pixel value 65535 = {hi:.9f} m.
Elevation in meters = pixel_value * {step:.12f} + {lo:.9f}.
Maximum quantization error is half a level ({step / 2:.6f} m).
Do not use the preview as a height map. Keep the mask separate; PNG contains
real terrain throughout the rectangle. Georeferencing lives in metadata.json
and the GeoTIFFs; PNG itself has no CRS. North is up, west is left.

The exact region matches the app's existing 2022 generalized Census city outline
plus an outward 804.672-meter buffer. That outline is a planning aid, not a
surveyed/current legal boundary. One-meter pixel spacing is not a claim of
one-meter boundary or vertical accuracy. Buildings and trees are excluded.

The current app uses point-query elevations; these are standalone project data
assets, not an automatic replacement of its elevation provider.

To regenerate, install numpy, rasterio, shapely, pyproj and pillow in a venv;
download the source URLs listed in metadata.json; save its sources array
as sources.json alongside the tiles; then run:

    python scripts/build_heightmap.py SOURCE_DIRECTORY OUTPUT_DIRECTORY
""")
        checksums = []
        for path in sorted(output_dir.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
                checksums.append(f"{digest}  {path.name}")
        (output_dir / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
        print(json.dumps({k: v for k, v in metadata.items() if k not in ("sources", "horizontal_wkt")}, indent=2))
    finally:
        for dataset in datasets:
            dataset.close()


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
