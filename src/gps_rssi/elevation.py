"""On-demand USGS ground elevations, cached separately from recorded GPS data."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import re
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .offline_store import OfflineStore

MAX_POINTS = 16
SOURCE = "USGS Elevation Point Query Service / 3DEP"
ENDPOINT = "https://epqs.nationalmap.gov/v1/json"
USER_AGENT = "GPS-RSSI/0.1 (+https://github.com/BoondockMark/GPS-RSSI)"


# EPQS resolution values can be in a source raster's native units. The output
# elevation's units=Meters parameter does not establish horizontal cell units.
RASTER_SERVICE = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"


def scalar(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()[:256]
        return text if text.lower() not in ("", "none", "null", "unknown", "n/a", "nodata") else None
    return None


def positive(value):
    try:
        result = float(value) if not isinstance(value, bool) else 0
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def cell_sizes(x, y, units, latitude):
    x, y = positive(x), positive(y)
    if x is None or y is None:
        return None
    unit = str(units).lower().strip()
    if unit in ('arcsecond', 'arcseconds', 'arc-second', 'arc-seconds', 'arcsec'):
        x, y, unit = x/3600, y/3600, 'degrees'
    factors = {'m': 1, 'meter': 1, 'meters': 1, 'metre': 1, 'metres': 1,
               'ft': .3048, 'foot': .3048, 'feet': .3048,
               'us survey foot': 1200/3937, 'us_survey_foot': 1200/3937}
    if unit in factors:
        sizes = (x*factors[unit], y*factors[unit])
        return sizes if all(math.isfinite(v) and v > 0 for v in sizes) else None
    if unit in ('degree', 'degrees'):
        metres = math.pi*6371000/180
        width = x*metres*abs(math.cos(math.radians(latitude)))
        sizes = (width, y*metres)
        return sizes if width > 1e-6 and all(math.isfinite(v) and v > 0 for v in sizes) else None
    return None


def raster_cells(info, latitude):
    """Interpret documented ArcGIS raster-info pixel sizes only with known units."""
    if not isinstance(info, dict):
        return None
    extent = info.get('extent') if isinstance(info.get('extent'), dict) else {}
    reference = extent.get('spatialReference') or info.get('spatialReference') or {}
    if not isinstance(reference, dict):
        return None
    wkid = reference.get('latestWkid', reference.get('wkid'))
    unit = info.get('units')
    if wkid in (4326, 4269):
        unit = 'degrees'
    elif isinstance(wkid, int) and (26901 <= wkid <= 26923 or 32601 <= wkid <= 32660 or
                                    32701 <= wkid <= 32760 or 6330 <= wkid <= 6354 or wkid == 5070):
        unit = 'meters'
    elif isinstance(reference.get('wkt'), str):
        wkt = reference['wkt']
        if ('PROJCS[' in wkt or 'PROJCRS[' in wkt) and not any(k in wkt for k in ('COMPD_CS[', 'COMPOUNDCRS[')):
            matches = re.findall(r'(?:UNIT|LENGTHUNIT)\s*\[\s*"([^"]+)"\s*,\s*([0-9.eE+-]+)', wkt)
            if matches and positive(matches[-1][1]):
                scale = positive(matches[-1][1])
                return cell_sizes(positive(info.get('pixelSizeX'))*scale if positive(info.get('pixelSizeX')) else None,
                                  positive(info.get('pixelSizeY'))*scale if positive(info.get('pixelSizeY')) else None, 'meters', latitude)
    # Unrecognized projected units remain unknown, including uncorrected Web Mercator.
    return cell_sizes(info.get('pixelSizeX'), info.get('pixelSizeY'), unit, latitude)


def point_metadata(data):
    attributes = data.get('attributes') if isinstance(data.get('attributes'), dict) else {}
    resolution = data.get('resolution')
    units = data.get('resolutionUnits') or attributes.get('ResolutionUnits')
    if isinstance(resolution, dict):
        units = resolution.get('units', units)
        resolution = resolution.get('value')
    return dict(source=SOURCE, raster_id=scalar(data.get('rasterId')),
                resolution_raw=scalar(resolution), resolution_units=scalar(units),
                acquisition_date=scalar(attributes.get('AcquisitionDate', data.get('acquisitionDate'))),
                source_name=scalar(attributes.get('Name') or attributes.get('Source') or data.get('dataSource')),
                resolution_m=None, cell_size_x_m=None, cell_size_y_m=None, resolution_basis='unverified units')


def coordinates(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_POINTS:
        raise ValueError(f"Supply 1 to {MAX_POINTS} elevation locations")
    result = []
    for point in values:
        if not isinstance(point, dict):
            raise ValueError("Each location needs latitude and longitude")
        lat, lon = point.get("lat"), point.get("lon")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (lat, lon)):
            raise ValueError("Coordinates must be finite numbers")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Coordinates are outside WGS84 bounds")
        result.append((round(lat, 6), round(lon, 6)))
    return result


class ElevationCache:
    def __init__(self, path):
        self.path = Path(path)
        self.offline = OfflineStore(self.path.parent.parent)
        self.busy = threading.Lock()
        self.slots = [threading.Lock() for _ in range(16)]
        self.raster_slots = [threading.Lock() for _ in range(16)]
        self.schema_lock = threading.Lock()
        self.schema_ready = False

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        with self.schema_lock:
            if not self.schema_ready:
                db.execute("CREATE TABLE IF NOT EXISTS elevation (lat REAL, lon REAL, value REAL, fetched REAL, metadata_json TEXT, pinned INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(lat,lon))")
                if 'metadata_json' not in {row[1] for row in db.execute('PRAGMA table_info(elevation)')}:
                    try:
                        db.execute('ALTER TABLE elevation ADD COLUMN metadata_json TEXT')
                    except sqlite3.OperationalError:
                        if 'metadata_json' not in {row[1] for row in db.execute('PRAGMA table_info(elevation)')}:
                            raise
                if 'pinned' not in {row[1] for row in db.execute('PRAGMA table_info(elevation)')}:
                    try:
                        db.execute('ALTER TABLE elevation ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
                    except sqlite3.OperationalError:
                        if 'pinned' not in {row[1] for row in db.execute('PRAGMA table_info(elevation)')}: raise
                db.execute("CREATE TABLE IF NOT EXISTS elevation_rasters (id TEXT PRIMARY KEY, info_json TEXT, fetched REAL, ok INTEGER)")
                self.schema_ready = True
        return db

    def raster_info(self, raster_id):
        # The fixed service and numeric catalog ID are never supplied as a URL.
        identifier = str(raster_id)
        if not re.fullmatch(r'[0-9]{1,12}', identifier):
            return None
        with self.raster_slots[hash(identifier) % len(self.raster_slots)]:
            with closing(self.connect()) as db:
                cached = db.execute('SELECT info_json,fetched,ok FROM elevation_rasters WHERE id=?', (identifier,)).fetchone()
            if cached and time.time()-cached[1] < (30*86400 if cached[2] else 3600):
                return json.loads(cached[0]) if cached[2] else None
            try:
                with urlopen(Request(f'{RASTER_SERVICE}/{identifier}/info?f=json', headers={'User-Agent': USER_AGENT}), timeout=5) as response:
                    raw = response.read(262145)
                info = json.loads(raw) if len(raw) <= 262144 else None
                if not isinstance(info, dict) or 'error' in info or positive(info.get('pixelSizeX')) is None:
                    info = None
                # Retain only fields needed to establish horizontal pixel dimensions.
                if info:
                    info = {key: info[key] for key in ('pixelSizeX','pixelSizeY','extent','spatialReference','units') if key in info}
                payload = json.dumps(info, allow_nan=False)
            except (OSError, ValueError, TypeError, OverflowError):
                info, payload = None, 'null'
            with closing(self.connect()) as db, db:
                db.execute('INSERT OR REPLACE INTO elevation_rasters VALUES (?,?,?,?)', (identifier,payload,time.time(),int(info is not None)))
                db.execute('DELETE FROM elevation_rasters WHERE id IN (SELECT id FROM elevation_rasters ORDER BY fetched DESC LIMIT -1 OFFSET 2048)')
            return info

    def get(self, point, *, refresh=False, pin=False):
        lat, lon = point
        with self.slots[hash(point) % len(self.slots)]:
            now = time.time()
            with closing(self.connect()) as db, db:
                cached = db.execute("SELECT value,fetched,metadata_json FROM elevation WHERE lat=? AND lon=?", point).fetchone()
                if cached and pin:
                    db.execute("UPDATE elevation SET pinned=1 WHERE lat=? AND lon=?",point)
            if cached and (self.offline.enabled or (now - cached[1] < 30 * 86400 and not refresh)):
                self.offline.resolved('elevation', f'{lat},{lon}')
                return dict(lat=lat, lon=lon, elevation=cached[0], fetched_at=cached[1], cached=True,
                            metadata=json.loads(cached[2]) if cached[2] else None, legacy_metadata=cached[2] is None, stale=now-cached[1]>=30*86400)
            if self.offline.enabled:
                self.offline.missing('elevation', f'{lat},{lon}')
                return dict(lat=lat,lon=lon,elevation=None,cached=False,error='offline-missing',metadata=None)
            query = urlencode(dict(x=lon, y=lat, units="Meters", wkid=4326, includeDate="True"))
            try:
                with urlopen(Request(ENDPOINT + "?" + query, headers={"User-Agent": USER_AGENT}), timeout=8) as response:
                    raw = response.read(65537)
                    if len(raw) > 65536:
                        raise OSError("Elevation response was too large")
            except OSError:
                self.offline.missing('elevation', f'{lat},{lon}')
                if cached:
                    return dict(lat=lat,lon=lon,elevation=cached[0],fetched_at=cached[1],cached=True,
                                metadata=json.loads(cached[2]) if cached[2] else None,legacy_metadata=cached[2] is None,stale=True)
                raise
            try:
                data = json.loads(raw)
                if isinstance(data["value"], bool):
                    raise ValueError("Boolean elevation")
                value = float(data["value"])
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise OSError("USGS returned an invalid elevation") from exc
            # EPQS uses a large negative sentinel for locations with no coverage.
            value = value if math.isfinite(value) and -500 <= value <= 9000 else None
            metadata = point_metadata(data)
            sizes = cell_sizes(metadata['resolution_raw'], metadata['resolution_raw'], metadata['resolution_units'], lat)
            if sizes:
                metadata['resolution_basis'] = 'EPQS explicit horizontal units'
            elif metadata['raster_id'] is not None:
                sizes = raster_cells(self.raster_info(metadata['raster_id']), lat)
                if sizes:
                    metadata['resolution_basis'] = 'USGS raster-info native pixels'
            if sizes:
                metadata.update(resolution_m=min(sizes), cell_size_x_m=sizes[0], cell_size_y_m=sizes[1])
            now = time.time()
            with closing(self.connect()) as db, db:
                db.execute("INSERT OR REPLACE INTO elevation (lat,lon,value,fetched,metadata_json,pinned) VALUES (?,?,?,?,?,MAX(?,COALESCE((SELECT pinned FROM elevation WHERE lat=? AND lon=?),0)))", (lat, lon, value, now, json.dumps(metadata, allow_nan=False),int(pin),lat,lon))
                count = db.execute("SELECT count(*) FROM elevation").fetchone()[0]
                if count > 50000:
                    db.execute("DELETE FROM elevation WHERE rowid IN (SELECT rowid FROM elevation WHERE pinned=0 ORDER BY fetched LIMIT ?)", (count - 50000,))
            self.offline.resolved('elevation', f'{lat},{lon}')
            return dict(lat=lat, lon=lon, elevation=value, fetched_at=now, cached=False, metadata=metadata, legacy_metadata=False)

    def get_many(self, values, *, refresh=False, pin=False):
        if not isinstance(refresh, bool):
            raise ValueError('Refresh must be a boolean')
        points = coordinates(values)
        if not self.busy.acquire(blocking=False):
            raise RuntimeError("Another elevation request is running; try again shortly")
        try:
            # Fail fast when the service is unreachable, before starting a batch.
            first = self.get(points[0], refresh=refresh, pin=pin)
            def read(point):
                try:
                    return self.get(point, refresh=refresh, pin=pin)
                except OSError:
                    return dict(lat=point[0], lon=point[1], elevation=None, error="unavailable")
            with ThreadPoolExecutor(max_workers=4) as pool:
                samples = [first, *pool.map(read, points[1:])]
            return dict(source=SOURCE, units="metres", samples=samples,
                        queried_at=datetime.now(timezone.utc).isoformat())
        finally:
            self.busy.release()
