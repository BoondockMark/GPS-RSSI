"""Elevation provenance, bounds, caching, and sandbox capability regression tests."""
from contextlib import closing
import http.client
import io
import json
from pathlib import Path
import re
import sqlite3
import time
import tempfile
import threading
import unittest
from unittest.mock import patch

from gps_rssi.elevation import ElevationCache, coordinates, raster_cells, cell_sizes
from gps_rssi.workspace_server import make_workspace_server, TERRAIN_CONFIG


class ElevationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = ElevationCache(Path(self.temp.name) / 'elevation.sqlite')

    def test_bounds_reject_bad_or_oversized_batches_before_network(self):
        for points in (None, [], [{}], [dict(lat=True, lon=1)], [dict(lat=float('nan'), lon=1)],
                       [dict(lat=91, lon=1)], [dict(lat=1, lon=181)], [dict(lat=1, lon=1)] * 17):
            with self.assertRaises(ValueError):
                coordinates(points)

    def test_requested_ground_elevations_are_identified_and_cached_across_restarts(self):
        with patch('gps_rssi.elevation.urlopen', return_value=io.BytesIO(b'{"value":"321.5"}')) as upstream:
            first = self.cache.get_many([dict(lat=34, lon=-118)])
            self.assertEqual(first['samples'][0]['elevation'], 321.5)
            request = upstream.call_args.args[0]
            self.assertIn('epqs.nationalmap.gov/v1/json?', request.full_url)
            self.assertIn('units=Meters', request.full_url)
            self.assertIn('includeDate=True', request.full_url)
            self.assertIn('GPS-RSSI', request.get_header('User-agent'))
            self.assertIn('USGS', first['source'])
        with patch('gps_rssi.elevation.urlopen', side_effect=OSError('offline')) as upstream:
            cached = ElevationCache(self.cache.path).get_many([dict(lat=34, lon=-118)])
            self.assertTrue(cached['samples'][0]['cached'])
            self.assertEqual(cached['samples'][0]['elevation'], 321.5)
            upstream.assert_not_called()

    def test_epqs_metadata_is_cached_without_conflating_retrieval_and_acquisition_dates(self):
        payload=dict(value=321.5,rasterId=123,resolution=10,resolutionUnits='Meters',
                     attributes=dict(AcquisitionDate='4/6/2015',Name='Example source DEM'))
        with patch('gps_rssi.elevation.urlopen',return_value=io.BytesIO(json.dumps(payload).encode())) as upstream:
            sample=self.cache.get_many([dict(lat=34,lon=-118)])['samples'][0]
            upstream.assert_called_once()
            self.assertEqual(sample['metadata']['resolution_m'],10)
            self.assertEqual(sample['metadata']['acquisition_date'],'4/6/2015')
            self.assertEqual(sample['metadata']['raster_id'],123)
        with patch('gps_rssi.elevation.urlopen',side_effect=AssertionError('cached')):
            cached=ElevationCache(self.cache.path).get_many([dict(lat=34,lon=-118)])['samples'][0]
            self.assertEqual(cached['metadata'],sample['metadata'])
            self.assertEqual(cached['fetched_at'],sample['fetched_at'])

    def test_native_raster_pixels_establish_units_and_raster_info_is_reused(self):
        point=dict(value=200,rasterId=123,resolution=1,attributes=dict(AcquisitionDate='2020-01-02'))
        info=dict(pixelSizeX=1,pixelSizeY=1,extent=dict(spatialReference=dict(wkid=26911)))
        responses=[io.BytesIO(json.dumps(p).encode()) for p in (point,info,point)]
        with patch('gps_rssi.elevation.urlopen',side_effect=responses) as upstream:
            first=self.cache.get_many([dict(lat=34,lon=-118)])['samples'][0]
            second=self.cache.get_many([dict(lat=34.001,lon=-118)])['samples'][0]
            self.assertEqual(first['metadata']['resolution_m'],1)
            self.assertEqual(second['metadata']['resolution_m'],1)
            self.assertEqual(upstream.call_count,3)
            self.assertIn('/ImageServer/123/info?f=json',upstream.call_args_list[1].args[0].full_url)

    def test_resolution_units_are_never_inferred_from_the_elevation_units_parameter(self):
        point=dict(value=200,rasterId=321,resolution=1)
        with patch('gps_rssi.elevation.urlopen',side_effect=[io.BytesIO(json.dumps(point).encode()),OSError('catalog unavailable')]):
            sample=self.cache.get_many([dict(lat=34,lon=-118)])['samples'][0]
            self.assertEqual(sample['metadata']['resolution_raw'],1)
            self.assertIsNone(sample['metadata']['resolution_m'])
            self.assertEqual(sample['elevation'],200)
        self.assertIsNone(raster_cells(dict(pixelSizeX=1,pixelSizeY=1,extent=dict(spatialReference=dict(wkid=999999))),34))

    def test_geographic_and_projected_native_units_are_converted_explicitly(self):
        info=dict(pixelSizeX=1/10800,pixelSizeY=1/10800,extent=dict(spatialReference=dict(wkid=4269)))
        x,y=raster_cells(info,34)
        self.assertGreater(x,8);self.assertLess(x,9);self.assertGreater(y,10);self.assertLess(y,11)
        self.assertAlmostEqual(cell_sizes(1,1,'feet',34)[0],.3048)
        self.assertAlmostEqual(cell_sizes(1/3,1/3,'arc-seconds',34)[0],x)
        self.assertIsNone(raster_cells(dict(extent=[]),34))

    def test_legacy_cache_migrates_without_losing_elevations_and_refresh_is_explicit(self):
        self.cache.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.cache.path) as db:
            db.execute('CREATE TABLE elevation (lat REAL,lon REAL,value REAL,fetched REAL,PRIMARY KEY(lat,lon))')
            db.execute('INSERT INTO elevation VALUES (34,-118,123,?)',(time.time(),))
        with patch('gps_rssi.elevation.urlopen',side_effect=AssertionError('must remain offline')):
            sample=self.cache.get_many([dict(lat=34,lon=-118)])['samples'][0]
            self.assertTrue(sample['legacy_metadata']);self.assertIsNone(sample['metadata'])
            self.assertEqual(sample['elevation'],123)
        payload=dict(value=124,resolution=10,resolutionUnits='Meters',attributes=dict(AcquisitionDate='2020'))
        with patch('gps_rssi.elevation.urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
            sample=self.cache.get_many([dict(lat=34,lon=-118)],refresh=True)['samples'][0]
            self.assertFalse(sample['legacy_metadata']);self.assertEqual(sample['metadata']['resolution_m'],10)
        with self.assertRaises(ValueError):
            self.cache.get_many([dict(lat=34,lon=-118)],refresh='yes')

    def test_no_data_is_unknown_and_network_failures_are_not_cached_as_zero(self):
        with patch('gps_rssi.elevation.urlopen', return_value=io.BytesIO(b'{"value":-1000000}')):
            self.assertIsNone(self.cache.get_many([dict(lat=34, lon=-118)])['samples'][0]['elevation'])
        with patch('gps_rssi.elevation.urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                self.cache.get_many([dict(lat=35, lon=-118)])
        with closing(self.cache.connect()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM elevation').fetchone()[0], 1)

    def test_zero_elevation_is_real_and_expired_entries_are_refetched(self):
        with patch('gps_rssi.elevation.urlopen', return_value=io.BytesIO(b'{"value":0}')):
            self.assertEqual(self.cache.get_many([dict(lat=34, lon=-118)])['samples'][0]['elevation'], 0)
        with closing(self.cache.connect()) as db, db:
            db.execute('UPDATE elevation SET fetched=0')
        with patch('gps_rssi.elevation.urlopen', return_value=io.BytesIO(b'{"value":101}')) as upstream:
            self.assertEqual(self.cache.get_many([dict(lat=34, lon=-118)])['samples'][0]['elevation'], 101)
            upstream.assert_called_once()

    def test_partial_failures_remain_unknown_and_busy_requests_are_bounded(self):
        with patch.object(self.cache, 'get', side_effect=[dict(elevation=100), OSError('offline')]):
            result = self.cache.get_many([dict(lat=34, lon=-118), dict(lat=35, lon=-118)])
            self.assertIsNone(result['samples'][1]['elevation'])
        with self.cache.busy:
            with self.assertRaises(RuntimeError):
                self.cache.get_many([dict(lat=34, lon=-118)])

    def test_malformed_elevations_do_not_enter_cache(self):
        for data in (b'bad json', b'{}', b'{"value":"invalid"}', b'{"value":true}'):
            with patch('gps_rssi.elevation.urlopen', return_value=io.BytesIO(data)):
                with self.assertRaises(OSError):
                    self.cache.get_many([dict(lat=34, lon=-118)])
        with closing(self.cache.connect()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM elevation').fetchone()[0], 0)


class ElevationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_workspace_server(self.temp.name, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        key = 'e' * 32
        folder = self.server.workspace.jobs.directory / key / 'result'
        folder.mkdir(parents=True)
        self.server.workspace.jobs.entries[key] = dict(id=key, kind='survey', title='Survey', state='completed')
        (folder / 'map.html').write_bytes(TERRAIN_CONFIG)
        self.report = f'/files/{key}/map.html'
        _, html, _ = self.request('GET', self.report)
        self.endpoint = json.loads(re.search(rb'>(.*)<', html)[1])['terrainEndpoint'].split(f':{self.server.server_port}', 1)[1]

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5); self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body=json.dumps(data) if data is not None else None,
                         headers={'Content-Type':'text/plain', **(headers or {})})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_opaque_report_can_query_only_elevations_without_workspace_privileges(self):
        opaque = {'Origin':'null', 'Sec-Fetch-Site':'cross-site'}
        with patch('gps_rssi.elevation.ElevationCache.get_many', return_value=dict(samples=[dict(elevation=100)])):
            status, _, headers = self.request('POST', self.endpoint, dict(points=[dict(lat=34,lon=-118)]), opaque)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Access-Control-Allow-Origin'], 'null')
        self.assertEqual(self.request('POST', '/api/jobs', dict(kind='preview'), opaque)[0], 403)
        self.assertEqual(self.request('POST', '/terrain/elevations?terrain_token=wrong', dict(points=[]), opaque)[0], 403)
        self.assertEqual(self.request('POST', self.endpoint, dict(points=[]), {'Origin':'https://attacker.example'})[0], 403)
        state = json.loads(self.request('GET','/api/state')[1])
        self.assertEqual(self.request('POST', '/terrain/elevations?terrain_token='+state['tile_token'], dict(points=[]), opaque)[0], 403)
        self.assertEqual(self.request('GET', self.report+'?download=1')[1], TERRAIN_CONFIG)

    def test_invalid_and_failed_elevation_requests_return_cors_readable_errors(self):
        opaque = {'Origin':'null'}
        status, _, headers = self.request('POST', self.endpoint, dict(points=[]), opaque)
        self.assertEqual(status, 400)
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'null')
        with patch('gps_rssi.elevation.ElevationCache.get_many', side_effect=OSError('offline')):
            self.assertEqual(self.request('POST', self.endpoint, dict(points=[dict(lat=34,lon=-118)]), opaque)[0], 503)
