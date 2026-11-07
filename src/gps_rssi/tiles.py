"""Bounded, persistent cache of OSM tiles requested by an interactive map.

No prefetch: only the visible browser viewport drives requests. Keep provider
expiry/validators and identify the app; never save error tiles or bypass caches.
"""
from contextlib import closing
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

USER_AGENT = "GPS-RSSI/0.1 (+https://github.com/BoondockMark/GPS-RSSI)"
DEFAULT_TTL = 7 * 86400
MAX_TILE_BYTES = 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n"
TILE_ROUTE = re.compile(r"/tiles/osm/(\d{1,2})/(\d{1,6})/(\d{1,6})\.png")


@dataclass(frozen=True)
class Tile:
    body: bytes
    expires: float
    etag: str | None = None
    modified: str | None = None
    no_store: bool = False


def expiry(headers, now):
    """Use upstream freshness; default to seven days only when unspecified."""
    control = headers.get("Cache-Control", "").lower()
    if re.search(r"(?:^|,)\s*(no-cache|no-store)\b", control):
        return now
    match = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)', control)
    try:
        if match:
            age = max(0, float(headers.get("Age", 0)))
            if headers.get("Date"):
                age = max(age, now - parsedate_to_datetime(headers["Date"]).timestamp())
            ttl = max(0, int(match[1]) - age)
            return now + ttl if math.isfinite(ttl) else now
        if headers.get("Expires"):
            return parsedate_to_datetime(headers["Expires"]).timestamp()
    except (ValueError, TypeError, OverflowError):
        return now  # Malformed freshness must not make a tile live forever.
    return now + DEFAULT_TTL


class TileCache:
    def __init__(self, path, *, max_bytes=128 * 1024 * 1024):
        self.path = Path(path)
        self.max_bytes = max_bytes
        # Bound both concurrent upstream work and per-tile lock memory.
        self.locks = [threading.Lock() for _ in range(16)]
        self.upstream_slots = threading.Semaphore(4)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("""CREATE TABLE IF NOT EXISTS tiles (
            key TEXT PRIMARY KEY, body BLOB NOT NULL, expires REAL NOT NULL,
            etag TEXT, modified TEXT, control TEXT, accessed REAL NOT NULL)""")
        return db

    def get(self, z, x, y, *, referer=None):
        if not (0 <= z <= 19 and 0 <= x < 2**z and 0 <= y < 2**z):
            raise ValueError("Invalid tile coordinates")
        key = f"{z}/{x}/{y}"
        with self.locks[hash(key) % len(self.locks)]:
            now = time.time()
            with closing(self.connect()) as db, db:
                previous = db.execute("SELECT body,expires,etag,modified,control FROM tiles WHERE key=?", (key,)).fetchone()
                if previous and previous[1] > now:
                    db.execute("UPDATE tiles SET accessed=? WHERE key=?", (now, key))
                    return Tile(*previous[:4])
            headers = {"User-Agent": USER_AGENT}
            if referer:
                headers["Referer"] = referer
            if previous:
                if previous[2]:
                    headers["If-None-Match"] = previous[2]
                if previous[3]:
                    headers["If-Modified-Since"] = previous[3]
            request = Request(f"https://tile.openstreetmap.org/{key}.png", headers=headers)
            not_modified = False
            with self.upstream_slots:
                try:
                    with urlopen(request, timeout=10) as response:
                        body = response.read(MAX_TILE_BYTES + 1)
                        if not body.startswith(PNG) or len(body) > MAX_TILE_BYTES:
                            raise ValueError("Tile server did not return a valid-sized PNG")
                        returned = response.headers
                except HTTPError as exc:
                    if exc.code != 304 or previous is None:
                        exc.close()
                        raise
                    not_modified = True
                    body = previous[0]
                    returned = exc.headers
                    exc.close()
            now = time.time()
            control = returned.get("Cache-Control", previous[4] if not_modified else "")
            freshness = dict(returned)
            # HTTPMessage names are case-insensitive; normalize for plain dictionaries.
            freshness = {k.title(): v for k, v in freshness.items()}
            freshness.setdefault("Cache-Control", control)
            no_store = bool(re.search(r"(?:^|,)\s*no-store\b", control.lower()))
            tile = Tile(body, expiry(freshness, now), returned.get("ETag", previous[2] if not_modified else None),
                        returned.get("Last-Modified", previous[3] if not_modified else None), no_store)
            with closing(self.connect()) as db, db:
                if no_store or len(body) > self.max_bytes:
                    db.execute("DELETE FROM tiles WHERE key=?", (key,))
                else:
                    db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?,?,?,?)",
                               (key, tile.body, tile.expires, tile.etag, tile.modified, control, now))
                    total = db.execute("SELECT COALESCE(SUM(length(body)),0) FROM tiles").fetchone()[0]
                    for old_key, size in db.execute("SELECT key,length(body) FROM tiles ORDER BY accessed,key").fetchall():
                        if total <= self.max_bytes:
                            break
                        db.execute("DELETE FROM tiles WHERE key=?", (old_key,))
                        total -= size
            return tile


def respond_tile(handler, cache, path):
    """Serve a validated route after the host/origin checks of the owning server."""
    match = TILE_ROUTE.fullmatch(path)
    try:
        if not match:
            raise ValueError("Invalid tile path")
        tile = cache.get(*(int(value) for value in match.groups()), referer=handler.headers.get("Referer"))
    except (OSError, ValueError, sqlite3.Error):
        # No retry or provider switching; the browser removes the background.
        data = b"Street tiles unavailable; measurements remain available."
        handler.send_response(503)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
    else:
        data = tile.body
        handler.send_response(200)
        handler.send_header("Content-Type", "image/png")
        handler.send_header("Cache-Control", "no-store" if tile.no_store else
                            f"private, max-age={max(0, int(tile.expires - time.time()))}")
        if tile.etag:
            handler.send_header("ETag", tile.etag)
        if tile.modified:
            handler.send_header("Last-Modified", tile.modified)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
