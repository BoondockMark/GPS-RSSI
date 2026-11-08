# La Habra Heights terrain data

## Download the prepared dataset

Open [la-habra-heights-1m.zip](../examples/la-habra-heights-1m.zip) on GitHub
and select **Download raw file**. The 266 MB ZIP is stored using Git LFS.
Unzip it to get the GeoTIFFs, 16-bit height image, planning mask, source-index
image, preview, metadata, and checksums. The raster covers the project's existing
La Habra Heights plus 0.5-mile planning buffer, with 1-meter cells.

For a local Git checkout, install Git LFS and run:

```sh
git lfs install
git lfs pull --include="examples/la-habra-heights-1m.zip"
```

If the file is a small text pointer starting with `version https://git-lfs`, the
large file has not been downloaded yet. GitHub source-code ZIP archives may also
contain pointers; use the individual file's download button or Git LFS instead.
Importing terrain into the app requires the local DEM support in PR #40.

## Local generated files

The generated dataset is in `data/heightmaps/la-habra-heights-1m/` and its portable
archive is `data/heightmaps/la-habra-heights-1m.zip`. Data files are intentionally
outside version control under the existing `data/` ignore rule. Keep the archive
with the project when copying it to another computer.
The prepared ZIP is also tracked under `examples/` using Git LFS. The working
files under `data/` remain outside version control.

Start with `terrain_1m_clipped.tif` in GIS software. It contains bare-earth
elevations in meters, clipped to the project's bundled city-plus-0.5-mile polygon.
For terrain engines, use `terrain_1m.tif` or `heightmap_16bit.png` and the scale in
the dataset's `metadata.json` and `README.md`. The rectangular versions also
include terrain outside the polygon; `planning_area_mask.png` identifies it.

These are standalone data assets. The app's current elevation provider still
uses USGS point queries; importing the rasters into that provider requires a
separate integration.

## Sources and reproduction

Query the USGS National Map API for `Digital Elevation Model (DEM) 1 meter`
with WGS84 bounds `-118.002,33.938,-117.907,33.984`:

<https://tnmaccess.nationalmap.gov/api/v1/products?bbox=-118.002,33.938,-117.907,33.984&datasets=Digital%20Elevation%20Model%20(DEM)%201%20meter&max=100>

Use the four `CA_LosAngeles_B23` tiles first. To cover gaps along the southern
buffer, use `x41y376` from `CA_SoCal_Wildfires_B1_2018`, then `x41y376` and
`x40y376` from `CA_LosAngeles_2016`. The archive's metadata retains the exact
product records, source order, URLs, and per-source pixel counts. All sources
have native 1-meter cells. Different surveys may produce seams.

The checked-in [source manifest](heightmap-sources.json) contains those seven
product records in precedence order, so regeneration does not require an
existing archive. In a dedicated Python 3.11+ virtual environment, install
`numpy rasterio shapely pyproj pillow`. From the repository root, download the
source tiles (approximately 2 GB) and build the outputs:

```sh
mkdir -p data/heightmaps/sources
cp docs/heightmap-sources.json data/heightmaps/sources/sources.json
python - <<'PY'
import json
from pathlib import Path
from urllib.request import urlretrieve

directory = Path("data/heightmaps/sources")
for source in json.loads((directory / "sources.json").read_text()):
    url = source["downloadURL"]
    path = directory / url.rsplit("/", 1)[1]
    print(f"Downloading {path.name}", flush=True)
    urlretrieve(url, path)
PY
```

```sh
python scripts/build_heightmap.py data/heightmaps/sources data/heightmaps/la-habra-heights-1m
```

The builder rejects incomplete coverage, checks common CRS and native cell size,
and writes a source-index image, GeoTIFFs, 16-bit height image, mask, preview,
provenance, scaling instructions, and checksums. The dataset uses NAD83 / UTM
zone 11N horizontally and NAVD88 meters vertically. Pixel spacing does not imply
equivalent vertical accuracy. The boundary is the existing generalized 2022
Census planning outline, not a surveyed municipal boundary.
