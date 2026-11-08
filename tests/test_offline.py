"""Prepared data must survive transfer and work with all upstream calls disabled."""
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from gps_rssi.elevation import ElevationCache
from gps_rssi.offline import OfflineData
from gps_rssi.tiles import TileCache, PNG

BODY=PNG+b'test raster'


def mbtiles(path, *, body=BODY, row=0, z=0, name='Regional topo'):
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE metadata(name TEXT,value TEXT); CREATE TABLE tiles(zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB);')
        db.executemany('INSERT INTO metadata VALUES (?,?)',[('name',name),('attribution','Example licensed source'),('format','png')])
        db.execute('INSERT INTO tiles VALUES (?,?,?,?)',(z,0,row,body))


def cached(data):
    tiles=TileCache(data/'.gps-rssi-tiles/cache.sqlite')
    elevation=ElevationCache(data/'.gps-rssi-elevation/cache.sqlite')
    with closing(tiles.connect()) as db, db:
        db.execute('INSERT INTO tiles VALUES (?,?,?,?,?,?,?,0)',('topo/3/2/4',BODY,1,'"v1"',None,'max-age=100',time.time()))
    with patch('gps_rssi.elevation.urlopen',return_value=io.BytesIO(json.dumps(dict(value=123,resolution=10,resolutionUnits='Meters',attributes=dict(AcquisitionDate='2020-01-02'))).encode())):
        elevation.get_many([dict(lat=34,lon=-118)])
    with closing(elevation.connect()) as db, db:
        db.execute('UPDATE elevation SET fetched=1')
    return tiles,elevation


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'first/data'
        self.offline=OfflineData(self.data)

    def test_pack_transfers_expired_maps_metadata_elevations_and_queue_without_network(self):
        tiles,elevations=cached(self.data)
        path=self.root/'topo.mbtiles';mbtiles(path)
        self.offline.import_mbtiles(path)
        self.offline.missing('elevation','34.1,-118.0')
        self.offline.set_enabled(True)
        pack=self.root/'prepared.zip';self.offline.export_pack(pack)
        destination=OfflineData(self.root/'second/data');destination.import_pack(pack)
        self.assertFalse(destination.enabled)  # Receiving machine explicitly chooses field mode.
        destination.set_enabled(True)
        with patch('gps_rssi.tiles.urlopen',side_effect=AssertionError('network forbidden')), patch('gps_rssi.elevation.urlopen',side_effect=AssertionError('network forbidden')):
            second_tiles=TileCache(destination.data/'.gps-rssi-tiles/cache.sqlite')
            self.assertEqual(second_tiles.get(3,2,4,provider='topo').body,BODY)
            self.assertEqual(second_tiles.get(0,0,0,provider='local').body,BODY)
            sample=ElevationCache(destination.data/'.gps-rssi-elevation/cache.sqlite').get_many([dict(lat=34,lon=-118)],refresh=True)['samples'][0]
            self.assertEqual(sample['elevation'],123)
            self.assertEqual(sample['metadata']['resolution_m'],10)
            self.assertEqual(sample['metadata']['acquisition_date'],'2020-01-02')
            self.assertEqual(sample['fetched_at'],1);self.assertTrue(sample['stale'])
        self.assertEqual(destination.status()['pending'],dict(elevation=1))
        with closing(tiles.connect()) as db:
            self.assertEqual(db.execute('SELECT pinned FROM tiles').fetchone()[0],1)
        with closing(elevations.connect()) as db:
            self.assertEqual(db.execute('SELECT pinned FROM elevation').fetchone()[0],1)

    def test_offline_misses_queue_every_point_and_do_not_refresh_or_substitute(self):
        self.offline.set_enabled(True)
        with patch('gps_rssi.tiles.urlopen',side_effect=AssertionError('network forbidden')), patch('gps_rssi.elevation.urlopen',side_effect=AssertionError('network forbidden')):
            with self.assertRaises(OSError): TileCache(self.data/'.gps-rssi-tiles/cache.sqlite').get(3,2,4,provider='topo')
            points=[dict(lat=34+i*.001,lon=-118) for i in range(4)]
            result=ElevationCache(self.data/'.gps-rssi-elevation/cache.sqlite').get_many(points)
            self.assertTrue(all(s['elevation'] is None for s in result['samples']))
        self.assertEqual(self.offline.status()['pending'],dict(tile=1,elevation=4))
        with self.assertRaises(ValueError): self.offline.fill()

    def test_fill_retries_missing_elevations_and_pins_successes(self):
        self.offline.missing('elevation','34.0,-118.0')
        with patch('gps_rssi.elevation.urlopen',return_value=io.BytesIO(b'{"value":234}')):
            result=self.offline.fill()
        self.assertEqual(result['filled'],1);self.assertEqual(result['pending'],{})
        with closing(ElevationCache(self.data/'.gps-rssi-elevation/cache.sqlite').connect()) as db:
            self.assertEqual(db.execute('SELECT pinned FROM elevation').fetchone()[0],1)
        for i in range(5): self.offline.missing('elevation',f'{35+i/100},-118.0')
        with patch('gps_rssi.elevation.urlopen',side_effect=OSError('offline')) as upstream:
            result=self.offline.fill()
        self.assertEqual(result['failed'],3);self.assertEqual(upstream.call_count,3)
        self.assertEqual(result['pending']['elevation'],5)

    def test_fill_does_not_count_stale_network_fallback_as_a_filled_gap(self):
        _,elevations=cached(self.data)
        self.offline.missing('elevation','34.0,-118.0')
        with patch('gps_rssi.elevation.urlopen',side_effect=OSError('offline')):
            result=self.offline.fill()
        self.assertEqual(result['filled'],0);self.assertEqual(result['failed'],1)
        self.assertEqual(result['pending']['elevation'],1)

    def test_mbtiles_tms_rows_are_flipped_and_invalid_input_keeps_previous_map(self):
        path=self.root/'map.mbtiles';mbtiles(path,z=2,row=1)
        self.offline.import_mbtiles(path)
        self.assertEqual(self.offline.tile(2,0,2),BODY)
        self.assertIsNone(self.offline.tile(2,0,1))
        bad=self.root/'vector.mbtiles';mbtiles(bad,body=b'PBF')
        with self.assertRaises(ValueError): self.offline.import_mbtiles(bad)
        self.assertEqual(self.offline.tile(2,0,2),BODY)

    def test_pack_rejects_external_schemas_and_does_not_partially_import_invalid_rows(self):
        cached(self.data)
        pack=self.root/'good.zip';self.offline.export_pack(pack)
        target=OfflineData(self.root/'second/data')
        with zipfile.ZipFile(pack) as archive:
            blobs={n:archive.read(n) for n in archive.namelist()}
        changed=self.root/'changed.sqlite';changed.write_bytes(blobs['elevation.sqlite'])
        with sqlite3.connect(changed) as db:
            db.execute('UPDATE elevation SET lat=999')
        blobs['elevation.sqlite']=changed.read_bytes()
        bad=self.root/'bad.zip'
        with zipfile.ZipFile(bad,'w') as archive:
            for name,body in blobs.items(): archive.writestr(name,body)
        with self.assertRaises(ValueError): target.import_pack(bad)
        self.assertEqual(target.status()['tiles'],0)
        evil=self.root/'evil.zip'
        with zipfile.ZipFile(evil,'w') as archive: archive.writestr('../cache.sqlite',b'')
        with self.assertRaises(ValueError): target.import_pack(evil)

    def test_import_keeps_newer_destination_values_and_pinned_rows_survive_eviction(self):
        tiles,_=cached(self.data);pack=self.root/'good.zip';self.offline.export_pack(pack)
        target=OfflineData(self.root/'second/data');other,_=cached(target.data)
        with closing(other.connect()) as db, db:
            db.execute('UPDATE tiles SET body=?,accessed=?',(PNG+b'newer',time.time()+10))
        target.import_pack(pack)
        with closing(other.connect()) as db:
            self.assertEqual(db.execute('SELECT body,pinned FROM tiles').fetchone(),(PNG+b'newer',1))
        # Force cleanup while a newly fetched unpinned tile exceeds the working budget.
        with patch('gps_rssi.tiles.urlopen') as upstream:
            response=io.BytesIO(BODY);response.headers={'Cache-Control':'max-age=100'}
            upstream.return_value=response
            TileCache(target.data/'.gps-rssi-tiles/cache.sqlite',max_bytes=len(BODY)).get(1,0,0)
        with closing(other.connect()) as db:
            self.assertEqual(db.execute('SELECT key FROM tiles').fetchall(),[('topo/3/2/4',)])

    def test_filled_tiles_are_pinned_before_working_cache_eviction(self):
        cache=TileCache(self.data/'.gps-rssi-tiles/cache.sqlite',max_bytes=len(BODY))
        with closing(cache.connect()) as db, db:
            db.execute('INSERT INTO tiles VALUES (?,?,?,?,?,?,?,1)',('3/1/1',BODY,1,None,None,'',time.time()))
        self.offline.missing('tile','topo/3/2/4')
        response=io.BytesIO(BODY);response.headers={'Cache-Control':'max-age=100'}
        with patch('gps_rssi.tiles.TileCache',return_value=cache),patch('gps_rssi.tiles.urlopen',return_value=response):
            result=self.offline.fill()
        self.assertEqual(result['filled'],1)
        with closing(cache.connect()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tiles WHERE pinned=1').fetchone()[0],2)

    def test_pack_snapshot_includes_committed_wal_data_with_writer_open(self):
        tiles,_=cached(self.data)
        with closing(tiles.connect()) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('INSERT INTO tiles VALUES (?,?,?,?,?,?,?,0)',('3/1/1',BODY,1,None,None,'',time.time()))
            writer.commit()
            self.assertTrue(Path(str(tiles.path)+'-wal').exists())
            pack=self.root/'wal.zip';self.offline.export_pack(pack)
            target=OfflineData(self.root/'second/data');target.import_pack(pack);target.set_enabled(True)
            with patch('gps_rssi.tiles.urlopen',side_effect=AssertionError('network forbidden')):
                self.assertEqual(TileCache(target.data/'.gps-rssi-tiles/cache.sqlite').get(3,1,1).body,BODY)

    def test_live_dashboard_uses_workspace_data_even_with_a_different_recording_folder(self):
        import threading
        from urllib.request import urlopen
        from gps_rssi.dashboard import make_server
        from gps_rssi.storage import Store
        tiles,_=cached(self.data);self.offline.set_enabled(True)
        db=self.root/'separate-recordings/mobile.sqlite'
        store=Store(db,'Mobile','mobile','replay',{});store.close()
        with patch.dict('os.environ',{'GPS_RSSI_MAP_DATA_DIRECTORY':str(self.data)}):
            server=make_server(db,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch('gps_rssi.tiles.urlopen',side_effect=AssertionError('network forbidden')):
                with urlopen(f'http://127.0.0.1:{server.server_port}/tiles/topo/3/2/4.png') as response:
                    self.assertEqual(response.read(),BODY)
        finally:
            server.shutdown();server.server_close();thread.join(5)

    def test_old_tile_schema_migrates_and_offline_mode_survives_restart(self):
        path=self.data/'.gps-rssi-tiles/cache.sqlite';path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE tiles(key TEXT PRIMARY KEY,body BLOB,expires REAL,etag TEXT,modified TEXT,control TEXT,accessed REAL)')
            db.execute('INSERT INTO tiles VALUES (?,?,?,?,?,?,?)',('3/2/4',BODY,1,None,None,'',1))
        self.offline.set_enabled(True)
        self.assertTrue(OfflineData(self.data).enabled)
        with patch('gps_rssi.tiles.urlopen',side_effect=AssertionError('network forbidden')):
            self.assertEqual(TileCache(path).get(3,2,4).body,BODY)
