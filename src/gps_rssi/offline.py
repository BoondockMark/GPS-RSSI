"""Import/export prepared map data and fill previously requested gaps."""
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import zipfile

from .offline_store import OfflineStore, readonly, MAX_PACK


class OfflineData(OfflineStore):
    def import_mbtiles(self, path):
        from .tiles import image_type, MAX_TILE_BYTES
        # Stage a clean database before replacing the one local basemap atomically.
        with closing(readonly(path)) as source, tempfile.TemporaryDirectory() as temp:
            metadata = dict(source.execute('SELECT name,value FROM metadata'))
            if metadata.get('scheme', 'tms') != 'tms':
                raise ValueError('MBTiles must use standard TMS tile rows')
            values = {k: str(metadata.get(k,''))[:2000] for k in ('name','attribution','description','bounds')}
            with sqlite3.connect(Path(temp)/'map.sqlite') as stage:
                stage.execute('CREATE TABLE tiles (z INTEGER,x INTEGER,y INTEGER,body BLOB,PRIMARY KEY(z,x,y))')
                count, size, lo, hi = 0, 0, 19, 0
                for z,x,row,body in source.execute('SELECT zoom_level,tile_column,tile_row,tile_data FROM tiles'):
                    if any(type(v) is not int for v in (z,x,row)) or not 0<=z<=19 or not 0<=x<2**z or not 0<=row<2**z:
                        raise ValueError('Invalid MBTiles coordinate (supported zooms: 0–19)')
                    if not isinstance(body,bytes) or len(body)>MAX_TILE_BYTES:
                        raise ValueError('Invalid or oversized MBTiles image')
                    image_type(body)
                    size += len(body); count += 1; lo=min(lo,z); hi=max(hi,z)
                    if size>MAX_PACK:
                        raise ValueError('Offline map exceeds 1 GB')
                    stage.execute('INSERT INTO tiles VALUES (?,?,?,?)',(z,x,2**z-1-row,body))
                if not count:
                    raise ValueError('MBTiles contains no raster tiles; vector PBF is unsupported')
                values.update(minzoom=str(lo),maxzoom=str(hi))
                with closing(self.connect()) as target, target:
                    target.execute('DELETE FROM map_tiles');target.execute('DELETE FROM map_metadata')
                    target.executemany('INSERT INTO map_tiles VALUES (?,?,?,?)',stage.execute('SELECT * FROM tiles'))
                    target.executemany('INSERT INTO map_metadata VALUES (?,?)',values.items())
        return self.status()

    def export_pack(self, output):
        """Snapshot live SQLite databases; do not copy a database without its WAL."""
        from .tiles import TileCache
        from .elevation import ElevationCache
        output=Path(output)
        if output.exists():
            raise FileExistsError(f'Output already exists: {output}')
        caches=[('tiles.sqlite',TileCache(self.data/'.gps-rssi-tiles/cache.sqlite'),'tiles'),
                ('elevation.sqlite',ElevationCache(self.data/'.gps-rssi-elevation/cache.sqlite'),'elevation')]
        with tempfile.TemporaryDirectory() as temp:
            for name,cache,table in caches:
                with closing(cache.connect()) as db, db:
                    db.execute(f'UPDATE {table} SET pinned=1')
                    with sqlite3.connect(Path(temp)/name) as copy:
                        db.commit();db.backup(copy)
            with closing(self.connect()) as db, sqlite3.connect(Path(temp)/'offline.sqlite') as copy:
                db.backup(copy)
            if sum((Path(temp)/name).stat().st_size for name in ('tiles.sqlite','elevation.sqlite','offline.sqlite'))>MAX_PACK-8192:
                raise ValueError('Offline pack exceeds the 1 GB expanded-size limit')
            with output.open('xb') as stream, zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json',json.dumps(dict(format='gps-rssi-offline',version=1,created_at=time.time())))
                for name in ('tiles.sqlite','elevation.sqlite','offline.sqlite'):
                    archive.write(Path(temp)/name,name)
        return self.status()

    def import_pack(self, path):
        """Validate every row in staging, then merge values without copying schemas."""
        from .tiles import TileCache, image_type, MAX_TILE_BYTES, SOURCES
        from .elevation import ElevationCache, coordinates
        with tempfile.TemporaryDirectory() as temp, zipfile.ZipFile(path) as archive:
            expected={'manifest.json','tiles.sqlite','elevation.sqlite','offline.sqlite'}
            if set(archive.namelist())!=expected or len(archive.infolist())!=4 or sum(i.file_size for i in archive.infolist())>MAX_PACK:
                raise ValueError('Invalid offline pack or expanded size over 1 GB')
            if archive.getinfo('manifest.json').file_size>8192: raise ValueError('Offline manifest is too large')
            manifest=json.loads(archive.read('manifest.json'))
            if not isinstance(manifest,dict): raise ValueError('Invalid offline manifest')
            if manifest.get('format')!='gps-rssi-offline' or manifest.get('version')!=1:
                raise ValueError('Unsupported offline pack version')
            for name in expected-{'manifest.json'}:
                with archive.open(name) as src, (Path(temp)/name).open('wb') as dst:
                    while block:=src.read(65536): dst.write(block)
            stage=OfflineData(Path(temp)/'clean')
            tiles=TileCache(stage.data/'.gps-rssi-tiles/cache.sqlite')
            elevations=ElevationCache(stage.data/'.gps-rssi-elevation/cache.sqlite')
            with closing(readonly(Path(temp)/'tiles.sqlite')) as src, closing(tiles.connect()) as dst, dst:
                for key,body,expires,etag,modified,control,accessed in src.execute('SELECT key,body,expires,etag,modified,control,accessed FROM tiles'):
                    match=re.fullmatch(r'(?:(osm|topo|relief|satellite)/)?(\d{1,2})/(\d{1,6})/(\d{1,6})',key)
                    if not match: raise ValueError('Invalid cached tile key')
                    provider,z,x,y=match.groups();z,x,y=map(int,(z,x,y))
                    if not 0<=z<=SOURCES[provider or 'osm']['maxNativeZoom'] or not 0<=x<2**z or not 0<=y<2**z: raise ValueError('Invalid cached tile coordinate')
                    if not isinstance(body,bytes) or len(body)>MAX_TILE_BYTES: raise ValueError('Invalid cached image')
                    image_type(body)
                    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (expires,accessed)): raise ValueError('Invalid cached tile time')
                    if any(v is not None and (not isinstance(v,str) or len(v)>4096) for v in (etag,modified,control)): raise ValueError('Invalid cache headers')
                    if 'no-store' in (control or '').lower(): continue
                    dst.execute('INSERT INTO tiles VALUES (?,?,?,?,?,?,?,1)',(key,body,expires,etag,modified,control,accessed))
            with closing(readonly(Path(temp)/'elevation.sqlite')) as src, closing(elevations.connect()) as dst, dst:
                for lat,lon,value,fetched,metadata in src.execute('SELECT lat,lon,value,fetched,metadata_json FROM elevation'):
                    coordinates([dict(lat=lat,lon=lon)])
                    if value is not None and (not isinstance(value,(int,float)) or not math.isfinite(value) or not -500<=value<=9000): raise ValueError('Invalid elevation')
                    if not isinstance(fetched,(int,float)) or not math.isfinite(fetched): raise ValueError('Invalid elevation time')
                    if metadata is not None and (not isinstance(metadata,str) or len(metadata)>8192 or not isinstance(json.loads(metadata),dict)): raise ValueError('Invalid elevation metadata')
                    if metadata is not None: json.dumps(json.loads(metadata),allow_nan=False)
                    dst.execute('INSERT INTO elevation VALUES (?,?,?,?,?,1)',(lat,lon,value,fetched,metadata))
            with closing(readonly(Path(temp)/'offline.sqlite')) as src, closing(stage.connect()) as dst, dst:
                for z,x,y,body in src.execute('SELECT z,x,y,body FROM map_tiles'):
                    if any(type(v) is not int for v in (z,x,y)) or not 0<=z<=19 or not 0<=x<2**z or not 0<=y<2**z or not isinstance(body,bytes) or len(body)>MAX_TILE_BYTES: raise ValueError('Invalid local map tile')
                    image_type(body);dst.execute('INSERT INTO map_tiles VALUES (?,?,?,?)',(z,x,y,body))
                for key,value in src.execute('SELECT key,value FROM map_metadata'):
                    if key not in ('name','attribution','description','bounds','minzoom','maxzoom') or not isinstance(value,str) or len(value)>2000: raise ValueError('Invalid map metadata')
                    if key in ('minzoom','maxzoom') and (not value.isdigit() or not 0<=int(value)<=19): raise ValueError('Invalid map zoom')
                    dst.execute('INSERT INTO map_metadata VALUES (?,?)',(key,value))
                for kind,key,requested in src.execute('SELECT kind,key,requested FROM missing'):
                    if kind not in ('tile','elevation') or not isinstance(key,str) or len(key)>100 or not isinstance(requested,(int,float)) or not math.isfinite(requested): raise ValueError('Invalid missing-data entry')
                    dst.execute('INSERT INTO missing VALUES (?,?,?)',(kind,key,requested))
            # All external bytes have been checked. Preserve newer destination cache rows.
            for source,target,table,columns in [(tiles,TileCache(self.data/'.gps-rssi-tiles/cache.sqlite'),'tiles','key,body,expires,etag,modified,control,accessed,pinned'),
                                                (elevations,ElevationCache(self.data/'.gps-rssi-elevation/cache.sqlite'),'elevation','lat,lon,value,fetched,metadata_json,pinned')]:
                with closing(source.connect()) as src, closing(target.connect()) as dst, dst:
                    for row in src.execute(f'SELECT {columns} FROM {table}'):
                        keys=('key',) if table=='tiles' else ('lat','lon')
                        stamp='accessed' if table=='tiles' else 'fetched'
                        newer=dst.execute(f'SELECT {stamp} FROM {table} WHERE '+ ' AND '.join(k+'=?' for k in keys),row[:len(keys)]).fetchone()
                        incoming=row[6] if table=='tiles' else row[3]
                        if not newer or newer[0]<=incoming:
                            dst.execute(f'INSERT OR REPLACE INTO {table} ({columns}) VALUES ('+','.join('?' for _ in row)+')',row)
                        else:
                            dst.execute(f'UPDATE {table} SET pinned=1 WHERE '+' AND '.join(k+'=?' for k in keys),row[:len(keys)])
            with closing(stage.connect()) as src, closing(self.connect()) as dst, dst:
                if src.execute('SELECT count(*) FROM map_tiles').fetchone()[0]:
                    dst.execute('DELETE FROM map_tiles');dst.execute('DELETE FROM map_metadata')
                    dst.executemany('INSERT INTO map_tiles VALUES (?,?,?,?)',src.execute('SELECT * FROM map_tiles'))
                    dst.executemany('INSERT INTO map_metadata VALUES (?,?)',src.execute('SELECT * FROM map_metadata'))
                dst.executemany('INSERT OR IGNORE INTO missing VALUES (?,?,?)',src.execute('SELECT * FROM missing'))
        return self.status()

    def fill(self, limit=1000):
        from .tiles import TileCache
        from .elevation import ElevationCache
        if self.enabled: raise ValueError('Turn off Offline mode before filling missing data')
        if type(limit) is not int or not 1<=limit<=5000: raise ValueError('Fill limit must be 1–5000')
        tiles=TileCache(self.data/'.gps-rssi-tiles/cache.sqlite')
        elevations=ElevationCache(self.data/'.gps-rssi-elevation/cache.sqlite')
        with closing(self.connect()) as db:
            rows=db.execute('SELECT kind,key FROM missing ORDER BY requested LIMIT ?', (limit,)).fetchall()
        done=failed=0
        for kind,key in rows:
            try:
                if kind=='tile':
                    provider,z,x,y=key.split('/')
                    if provider=='local': continue  # Local map gaps need another MBTiles file.
                    tile=tiles.get(int(z),int(x),int(y),provider=provider,pin=True)
                    if tile.no_store:
                        self.missing('tile',key)
                        raise ValueError('Provider disallows storing this tile')
                else:
                    lat,lon=map(float,key.split(','));elevations.get_many([dict(lat=lat,lon=lon)],pin=True)
                with closing(self.connect()) as db:
                    if db.execute('SELECT 1 FROM missing WHERE kind=? AND key=?',(kind,key)).fetchone():
                        raise OSError('Lookup did not fill the queued gap')
                done+=1
            except (OSError,ValueError,sqlite3.Error):
                failed+=1
                if failed>=3: break  # Avoid repeated timeouts without connectivity.
            print(f'Filled {done}; failed {failed}; pending snapshot {len(rows)}',flush=True)
        return dict(filled=done,failed=failed,**self.status())
