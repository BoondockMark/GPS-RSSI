"""Portable offline maps, cache snapshots, and a persistent missing-data queue.

MBTiles inputs must contain raster PNG/JPEG tiles with the standard TMS row order.
Public interactive tile endpoints are never bulk-prefetched here.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

MAX_PACK = 1024 * 1024 * 1024


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    db.set_progress_handler(lambda: 1, 20000000)  # Reject expensive views in untrusted inputs.
    return db


class OfflineStore:
    def __init__(self, data):
        self.data = Path(data)
        self.path = self.data / '.gps-rssi-offline/data.sqlite'

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.executescript('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS missing (kind TEXT, key TEXT, requested REAL, PRIMARY KEY(kind,key));
            CREATE TABLE IF NOT EXISTS map_tiles (z INTEGER,x INTEGER,y INTEGER,body BLOB,PRIMARY KEY(z,x,y));
            CREATE TABLE IF NOT EXISTS map_metadata (key TEXT PRIMARY KEY,value TEXT);''')
        return db

    @property
    def enabled(self):
        if not self.path.exists():
            return False
        with closing(self.connect()) as db:
            row = db.execute("SELECT value FROM settings WHERE key='offline'").fetchone()
        return bool(row and row[0] == 'true')

    def set_enabled(self, value):
        if not isinstance(value, bool):
            raise ValueError('Offline mode must be a boolean')
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('offline',?)", (json.dumps(value),))

    def missing(self, kind, key):
        with closing(self.connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO missing VALUES (?,?,?)', (kind, key, time.time()))
            db.execute('DELETE FROM missing WHERE rowid IN (SELECT rowid FROM missing ORDER BY requested DESC LIMIT -1 OFFSET 50000)')

    def resolved(self, kind, key):
        if self.path.exists():
            with closing(self.connect()) as db, db:
                db.execute('DELETE FROM missing WHERE kind=? AND key=?', (kind, key))

    def map_config(self):
        if not self.path.exists():
            return None
        with closing(self.connect()) as db:
            values = dict(db.execute('SELECT key,value FROM map_metadata'))
        return values or None

    def tile(self, z, x, y):
        if not self.path.exists():
            return None
        with closing(self.connect()) as db:
            row = db.execute('SELECT body FROM map_tiles WHERE z=? AND x=? AND y=?', (z,x,y)).fetchone()
        return row[0] if row else None

    def status(self):
        counts = {}
        for kind, folder, table in [('tiles','.gps-rssi-tiles','tiles'),('elevations','.gps-rssi-elevation','elevation')]:
            path = self.data / folder / 'cache.sqlite'
            counts[kind] = 0
            if path.exists():
                with closing(readonly(path)) as db:
                    counts[kind] = db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        if self.path.exists():
            with closing(self.connect()) as db:
                counts.update(local_tiles=db.execute('SELECT count(*) FROM map_tiles').fetchone()[0],
                              pending=dict(db.execute('SELECT kind,count(*) FROM missing GROUP BY kind')))
        else:
            counts.update(local_tiles=0,pending={})
        return dict(offline=self.enabled, map=self.map_config(), **counts)

