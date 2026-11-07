"""Tile caching and HTTP integration without contacting the public tile service."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen as local_urlopen

from gps_rssi.dashboard import make_server
from gps_rssi.storage import Store
from gps_rssi.tiles import DEFAULT_TTL, PNG, TileCache, USER_AGENT, expiry
from gps_rssi.workspace_server import make_workspace_server


BODY = PNG + b'test tile'


def headers(**kwargs):
    result = Message()
    for key, value in kwargs.items():
        result[key.replace('_', '-')] = value
    return result


class Response(io.BytesIO):
    def __init__(self, body=BODY, **values):
        super().__init__(body)
        self.headers = headers(**values)


class TileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tiles/cache.sqlite'
        self.cache = TileCache(self.path)

    def test_fresh_tiles_survive_restart_and_avoid_network(self):
        with patch('gps_rssi.tiles.urlopen', return_value=Response(Cache_Control='max-age=3600', ETag='"v1"')) as upstream:
            first = self.cache.get(3, 2, 4, referer='http://localhost:8780/')
            self.assertEqual(first.body, BODY)
            request = upstream.call_args.args[0]
            self.assertEqual(request.full_url, 'https://tile.openstreetmap.org/3/2/4.png')
            self.assertEqual(request.get_header('User-agent'), USER_AGENT)
            self.assertEqual(request.get_header('Referer'), 'http://localhost:8780/')
            self.assertIsNone(request.get_header('Cache-control'))
        with patch('gps_rssi.tiles.urlopen', side_effect=OSError('offline')) as upstream:
            self.assertEqual(TileCache(self.path).get(3, 2, 4).body, BODY)
            upstream.assert_not_called()

    def test_expired_tiles_revalidate_with_etag_and_last_modified(self):
        modified = 'Wed, 21 Oct 2015 07:28:00 GMT'
        with patch('gps_rssi.tiles.urlopen', return_value=Response(Cache_Control='max-age=0', ETag='"v1"', Last_Modified=modified)):
            self.cache.get(3, 2, 4)
        not_changed = HTTPError('url', 304, 'Not Modified', headers(Cache_Control='max-age=7200'), io.BytesIO())
        with patch('gps_rssi.tiles.urlopen', side_effect=not_changed) as upstream:
            tile = self.cache.get(3, 2, 4)
            request = upstream.call_args.args[0]
            self.assertEqual(request.get_header('If-none-match'), '"v1"')
            self.assertEqual(request.get_header('If-modified-since'), modified)
            self.assertGreater(tile.expires, time.time() + 7100)
        with patch('gps_rssi.tiles.urlopen') as upstream:
            self.assertEqual(self.cache.get(3, 2, 4).body, BODY)
            upstream.assert_not_called()

    def test_updated_tiles_do_not_keep_old_validators_or_expiry(self):
        with patch('gps_rssi.tiles.urlopen', return_value=Response(Cache_Control='max-age=0', ETag='"old"')):
            self.cache.get(1, 0, 0)
        with patch('gps_rssi.tiles.urlopen', return_value=Response(PNG + b'new')):
            tile = self.cache.get(1, 0, 0)
        self.assertIsNone(tile.etag)
        self.assertGreater(tile.expires, time.time() + DEFAULT_TTL - 5)

    def test_expiry_honors_provider_directives_and_missing_headers(self):
        self.assertEqual(expiry({}, 10), DEFAULT_TTL + 10)
        self.assertEqual(expiry({'Cache-Control': 'max-age=100', 'Age': '40'}, 10), 70)
        self.assertEqual(expiry({'Cache-Control': 'no-cache, max-age=100'}, 10), 10)
        self.assertEqual(expiry({'Cache-Control': 'no-store, max-age=100'}, 10), 10)
        self.assertEqual(expiry({'Expires': 'Thu, 01 Jan 1970 00:02:00 GMT'}, 10), 120)

    def test_no_store_and_error_responses_are_never_cached(self):
        for response in (Response(Cache_Control='no-store'), Response(b'Access blocked')):
            with patch('gps_rssi.tiles.urlopen', return_value=response):
                try:
                    self.cache.get(1, 0, 0)
                except ValueError:
                    pass
            with closing(self.cache.connect()) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM tiles').fetchone()[0], 0)
        error = HTTPError('url', 403, 'Access blocked', headers(), io.BytesIO(BODY))
        with patch('gps_rssi.tiles.urlopen', side_effect=error):
            with self.assertRaises(HTTPError):
                self.cache.get(1, 0, 0)
        with closing(self.cache.connect()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tiles').fetchone()[0], 0)

    def test_concurrent_requests_for_same_tile_fetch_only_once(self):
        with patch('gps_rssi.tiles.urlopen', side_effect=lambda *a, **k: Response(Cache_Control='max-age=100')) as upstream:
            with ThreadPoolExecutor(max_workers=6) as pool:
                tiles = list(pool.map(lambda _: self.cache.get(3, 2, 4), range(6)))
            self.assertEqual(len(tiles), 6)
            self.assertEqual(upstream.call_count, 1)

    def test_tile_budget_evicts_least_recently_used(self):
        cache = TileCache(self.path, max_bytes=len(BODY) * 2)
        with patch('gps_rssi.tiles.urlopen', side_effect=lambda *a, **k: Response(Cache_Control='max-age=100')):
            cache.get(2, 0, 0)
            cache.get(2, 1, 0)
            cache.get(2, 0, 0)
            cache.get(2, 2, 0)
        with closing(cache.connect()) as db:
            self.assertEqual({row[0] for row in db.execute('SELECT key FROM tiles')}, {'2/0/0', '2/2/0'})

    def test_bad_coordinates_never_contact_upstream(self):
        with patch('gps_rssi.tiles.urlopen') as upstream:
            for coords in ((20, 0, 0), (-1, 0, 0), (1, 2, 0), (1, 0, 2), (1, -1, 0)):
                with self.assertRaises(ValueError):
                    self.cache.get(*coords)
            upstream.assert_not_called()

    def test_live_dashboard_serves_and_reuses_cached_tiles(self):
        path = Path(self.temp.name) / 'data/radio.sqlite'
        store = Store(path, 'test', 'mobile', 'live', {})
        store.close()
        server = make_server(path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        try:
            with patch('gps_rssi.tiles.urlopen', return_value=Response(Cache_Control='max-age=3600')) as upstream:
                for _ in range(2):
                    with local_urlopen(origin + '/tiles/osm/3/2/4.png', timeout=3) as response:
                        self.assertEqual(response.read(), BODY)
                self.assertEqual(upstream.call_count, 1)
            with patch('gps_rssi.tiles.urlopen', return_value=Response(Cache_Control='no-store')):
                with local_urlopen(origin + '/tiles/osm/3/3/4.png', timeout=3) as response:
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with local_urlopen(origin + '/assets/basemap.js', timeout=3) as response:
                self.assertIn(b'RssiBasemap', response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_workspace_cache_capability_only_grants_tiles_to_sandboxed_pages(self):
        server = make_workspace_server(Path(self.temp.name), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        try:
            with local_urlopen(origin + '/api/state', timeout=3) as response:
                token = json.load(response)['tile_token']
            request = Request(origin + '/tiles/osm/3/2/4.png?tile_token=' + token, headers={'Sec-Fetch-Site':'cross-site'})
            with patch('gps_rssi.tiles.urlopen', return_value=Response(Cache_Control='max-age=3600')) as upstream:
                for _ in range(2):
                    with local_urlopen(request, timeout=3) as response:
                        self.assertEqual(response.read(), BODY)
                        self.assertIn('max-age=', response.headers['Cache-Control'])
                self.assertEqual(upstream.call_count, 1)
            for path in ('/tiles/osm/3/2/4.png', '/tiles/osm/3/2/4.png?tile_token=wrong', '/api/state?tile_token=' + token):
                request = Request(origin + path, headers={'Sec-Fetch-Site':'cross-site', 'Referer':'https://attacker.example/'})
                with self.assertRaises(HTTPError) as rejected:
                    local_urlopen(request, timeout=3)
                self.assertEqual(rejected.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
