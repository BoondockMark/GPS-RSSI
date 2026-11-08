"""On-demand USGS ground elevations, cached separately from recorded GPS data."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MAX_POINTS = 16
SOURCE = "USGS Elevation Point Query Service / 3DEP"
ENDPOINT = "https://epqs.nationalmap.gov/v1/json"
USER_AGENT = "GPS-RSSI/0.1 (+https://github.com/BoondockMark/GPS-RSSI)"


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
        self.busy = threading.Lock()
        self.slots = [threading.Lock() for _ in range(16)]

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("CREATE TABLE IF NOT EXISTS elevation (lat REAL, lon REAL, value REAL, fetched REAL, PRIMARY KEY(lat,lon))")
        return db

    def get(self, point):
        lat, lon = point
        with self.slots[hash(point) % len(self.slots)]:
            now = time.time()
            with closing(self.connect()) as db:
                cached = db.execute("SELECT value,fetched FROM elevation WHERE lat=? AND lon=?", point).fetchone()
            if cached and now - cached[1] < 30 * 86400:
                return dict(lat=lat, lon=lon, elevation=cached[0], fetched_at=cached[1], cached=True)
            query = urlencode(dict(x=lon, y=lat, units="Meters", wkid=4326, includeDate="False"))
            with urlopen(Request(ENDPOINT + "?" + query, headers={"User-Agent": USER_AGENT}), timeout=8) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise OSError("Elevation response was too large")
            try:
                data = json.loads(raw)
                if isinstance(data["value"], bool):
                    raise ValueError("Boolean elevation")
                value = float(data["value"])
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise OSError("USGS returned an invalid elevation") from exc
            # EPQS uses a large negative sentinel for locations with no coverage.
            value = value if math.isfinite(value) and -500 <= value <= 9000 else None
            with closing(self.connect()) as db, db:
                db.execute("INSERT OR REPLACE INTO elevation VALUES (?,?,?,?)", (lat, lon, value, now))
                count = db.execute("SELECT count(*) FROM elevation").fetchone()[0]
                if count > 50000:
                    db.execute("DELETE FROM elevation WHERE rowid IN (SELECT rowid FROM elevation ORDER BY fetched LIMIT ?)", (count - 50000,))
            return dict(lat=lat, lon=lon, elevation=value, fetched_at=now, cached=False)

    def get_many(self, values):
        points = coordinates(values)
        if not self.busy.acquire(blocking=False):
            raise RuntimeError("Another elevation request is running; try again shortly")
        try:
            # Fail fast when the service is unreachable, before starting a batch.
            first = self.get(points[0])
            def read(point):
                try:
                    return self.get(point)
                except OSError:
                    return dict(lat=point[0], lon=point[1], elevation=None, error="unavailable")
            with ThreadPoolExecutor(max_workers=4) as pool:
                samples = [first, *pool.map(read, points[1:])]
            return dict(source=SOURCE, units="metres", samples=samples,
                        queried_at=datetime.now(timezone.utc).isoformat())
        finally:
            self.busy.release()
