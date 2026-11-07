import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from gps_rssi.tiles import TileCache, PNG_SIGNATURE, USER_AGENT


class TileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = TileCache(self.temp.name)

    def response(self, data=PNG_SIGNATURE + b"fixture", headers=None):
        response = io.BytesIO(data)
        response.headers = headers or {}
        return response

    def test_identified_requests_are_cached_across_server_restarts(self):
        with patch("gps_rssi.tiles.urlopen", return_value=self.response()) as fetch:
            data = self.cache.get("2/1/1.png")
            request = fetch.call_args.args[0]
            self.assertEqual(request.full_url, "https://tile.openstreetmap.org/2/1/1.png")
            self.assertEqual(request.get_header("User-agent"), USER_AGENT)
            self.assertEqual(TileCache(self.temp.name).get("2/1/1.png"), data)
            fetch.assert_called_once()

    def test_invalid_coordinates_cannot_trigger_network_requests(self):
        with patch("gps_rssi.tiles.urlopen") as fetch:
            for value in ("20/1/1.png", "1/2/0.png", "1/0/2.png", "../secret", "https://example.com/0/0/0.png"):
                with self.assertRaises(ValueError):
                    self.cache.get(value)
            fetch.assert_not_called()

    def test_expired_tiles_are_refetched_and_failures_are_not_cached(self):
        with patch("gps_rssi.tiles.urlopen", side_effect=[self.response(headers={"Cache-Control": "max-age=0"}), self.response()]) as fetch:
            self.cache.get("2/1/1.png")
            self.cache.get("2/1/1.png")
            self.assertEqual(fetch.call_count, 2)
        for response in (self.response(b"Access blocked"), HTTPError("url", 403, "Forbidden", {}, None)):
            with patch("gps_rssi.tiles.urlopen", side_effect=response if isinstance(response, Exception) else None,
                       return_value=response):
                with self.assertRaises(OSError):
                    self.cache.get("2/0/0.png")
            self.assertFalse((Path(self.temp.name) / "2/0/0.png").exists())
