"""Managed, local bare-earth GeoTIFF sampling without retaining open file handles."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid


class LocalDEM:
    def __init__(self, data):
        self.directory = Path(data) / '.gps-rssi-dem'
        self.manifest = self.directory / 'current.json'

    def config(self):
        if not self.manifest.exists():
            return None
        data = json.loads(self.manifest.read_text(encoding='utf-8'))
        if not re.fullmatch(r'[0-9a-f]{32}\.tif', data.get('file', '')):
            raise ValueError('Invalid local terrain file reference')
        return data

    @contextmanager
    def open(self, path):
        try:
            import rasterio
        except ImportError as exc:
            raise ValueError('Update GPS-RSSI with its installer to enable GeoTIFF terrain support') from exc
        # Only standalone TIFFs, without sidecar files, virtual rasters or network URLs.
        with rasterio.Env(GDAL_PAM_ENABLED='NO', GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR'):
            with rasterio.open(Path(path), driver='GTiff') as src:
                yield src

    def validate(self, path):
        with self.open(path) as src:
            tags = src.tags()
            if src.count != 1 or src.crs is None or src.crs.to_epsg() != 26911:
                raise ValueError('Choose a single-band NAD83 / UTM zone 11N (EPSG:26911) terrain GeoTIFF')
            if tags.get('vertical_datum', '').upper() != 'NAVD88' or tags.get('units', '').lower() not in ('meters', 'metres', 'm'):
                raise ValueError('Terrain must declare vertical_datum=NAVD88 and units=meters; use the generated terrain_1m GeoTIFF')
            if src.transform.b or src.transform.d or src.transform.a <= 0 or src.transform.e >= 0:
                raise ValueError('Terrain must use a north-up grid')
            if not all(math.isfinite(v) for v in (*src.transform, *src.bounds)):
                raise ValueError('Terrain grid coordinates must be finite')
            if src.dtypes[0] not in ('int16', 'uint16', 'int32', 'uint32', 'float32', 'float64'):
                raise ValueError('Terrain must contain numeric elevation cells')
            if src.scales != (1.0,) or src.offsets != (0.0,):
                raise ValueError('Terrain must contain unscaled elevations in meters')
            if src.width * src.height > 250_000_000:
                raise ValueError('Terrain exceeds 250 million cells')
            valid = 0
            for _, window in src.block_windows(1):
                pixels = src.read(1, window=window, masked=True)
                values = pixels.compressed()
                if values.size:
                    if not all(math.isfinite(float(v)) for v in (values.min(), values.max())) or not ((values >= -500) & (values <= 9000)).all():
                        raise ValueError('Terrain contains invalid elevations (expected -500 to 9000 meters)')
                    valid += values.size
            if not valid:
                raise ValueError('Terrain contains no valid elevations')
            return dict(source=tags.get('source', 'Imported bare-earth terrain')[:2000],
                        vertical_datum='NAVD88', units='meters', crs='EPSG:26911',
                        width=src.width, height=src.height, bounds=list(src.bounds),
                        cell_size_x_m=src.res[0], cell_size_y_m=src.res[1], valid_cells=int(valid))

    def install(self, path):
        path = Path(path)
        if not 0 < path.stat().st_size <= 1024 ** 3:
            raise ValueError('Terrain GeoTIFF must be at most 1 GB')
        self.directory.mkdir(parents=True, exist_ok=True)
        # A unique file plus atomic pointer replacement permits concurrent readers.
        name = uuid.uuid4().hex + '.tif'
        target = self.directory / name
        try:
            shutil.copyfile(path, target)
            config = self.validate(target)
            with target.open('rb') as stream:
                config.update(sha256=hashlib.file_digest(stream, 'sha256').hexdigest())
            config.update(file=name, imported_at=time.time())
            with tempfile.NamedTemporaryFile(dir=self.directory, mode='w', encoding='utf-8', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(config, stream, allow_nan=False)
            try:
                temporary.replace(self.manifest)
            finally:
                temporary.unlink(missing_ok=True)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        # Keep previous files until explicit maintenance so in-flight readers work on Windows too.
        return config

    def remove(self):
        self.manifest.unlink(missing_ok=True)

    def sample(self, lat, lon):
        config = self.config()
        if config is None:
            return None
        from rasterio.warp import transform
        from rasterio.windows import Window
        with self.open(self.directory / config['file']) as src:
            xs, ys = transform('EPSG:4326', src.crs, [lon], [lat])
            row, col = src.index(xs[0], ys[0])
            if not (0 <= row < src.height and 0 <= col < src.width):
                return None
            pixel = src.read(1, window=Window(col, row, 1, 1), masked=True)
            if bool(pixel.mask.any()):
                return None
            value = float(pixel[0, 0])
            if not math.isfinite(value) or not -500 <= value <= 9000:
                return None
        metadata = dict(source='Local terrain GeoTIFF', source_name=config['source'],
                        raster_id=config['sha256'], resolution_raw=config['cell_size_x_m'],
                        resolution_units='meters', resolution_m=min(config['cell_size_x_m'], config['cell_size_y_m']),
                        cell_size_x_m=config['cell_size_x_m'], cell_size_y_m=config['cell_size_y_m'],
                        resolution_basis='Local GeoTIFF native pixels; nearest cell',
                        vertical_datum='NAVD88', acquisition_date=None)
        return dict(lat=lat, lon=lon, elevation=value, fetched_at=config['imported_at'], cached=False,
                    local=True, stale=False, legacy_metadata=False, metadata=metadata)
