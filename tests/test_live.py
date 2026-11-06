import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from gps_rssi.cli import parser
from gps_rssi.live import area_overlay, make_server, snapshot
from gps_rssi.storage import Store
from test_analysis import FRAME


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "live.sqlite"
        self.writer = Store(self.path, "mobile-uvpro", "mobile", "btech_bluetooth", {})
        self.addCleanup(self.writer.close)

    def test_incremental_reads_follow_a_running_writer_without_duplicates(self):
        first = snapshot(self.path)
        self.assertEqual(first["points"], [])
        self.writer.sample(FRAME)
        next_read = snapshot(self.path, after=first["cursor"], generation=first["generation"])
        self.assertFalse(next_read["reset"])
        self.assertEqual(len(next_read["points"]), 1)
        idle = snapshot(self.path, after=next_read["cursor"], generation=next_read["generation"])
        self.assertEqual(idle["points"], [])
        self.assertEqual(idle["processed"], 0)
        self.assertEqual(idle["cursor"], next_read["cursor"])

    def test_bad_gps_advances_cursor_and_is_counted(self):
        bad = copy.deepcopy(FRAME)
        bad["status"]["is_gps_locked"] = False
        self.writer.sample(bad)
        first = snapshot(self.path)
        self.assertEqual(first["excluded"], {"no_gps_lock": 1})
        self.assertEqual(first["cursor"], 1)
        self.writer.sample(FRAME)
        second = snapshot(self.path, after=1, generation=first["generation"])
        self.assertEqual(len(second["points"]), 1)
        self.assertEqual(second["points"][0]["id"], 2)

    def test_initial_window_uses_newest_selected_samples(self):
        for _ in range(5):
            self.writer.sample(FRAME)
        result = snapshot(self.path, max_points=2)
        self.assertEqual([p["id"] for p in result["points"]], [4, 5])
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["cursor"], 5)

    def test_changed_database_generation_resets_high_cursor(self):
        self.writer.sample(FRAME)
        result = snapshot(self.path, after=9999, generation="old-database")
        self.assertTrue(result["reset"])
        self.assertEqual(result["cursor"], 1)
        self.assertEqual(len(result["points"]), 1)

    def test_filters_and_outside_points_are_preserved(self):
        outside = copy.deepcopy(FRAME)
        outside["position"].update(latitude=34.2, longitude=-118.2)
        self.writer.sample(outside)
        with_replay = Store(self.path, "demo", "mobile", "replay", {})
        try:
            with_replay.sample(FRAME)
        finally:
            with_replay.close()
        live = snapshot(self.path)
        self.assertEqual(len(live["points"]), 1)
        self.assertEqual(live["points"][0]["lat"], 34.2)
        self.assertEqual(len(snapshot(self.path, source="all")["points"]), 2)
        self.assertEqual(len(snapshot(self.path, station="demo")["points"]), 0)

    def test_link_events_and_sample_state_are_exposed(self):
        self.writer.event("connection_error", {"stage": "status"})
        self.writer.sample(FRAME)
        result = snapshot(self.path)
        self.assertEqual(result["link_event"]["kind"], "connection_error")
        self.assertEqual(result["latest"]["rssi_value"], 0)
        self.assertEqual(result["latest"]["gps_locked"], 1)

    def test_read_does_not_mutate_log(self):
        self.writer.sample(FRAME)
        before = self.writer.db.total_changes
        snapshot(self.path)
        self.assertEqual(self.writer.db.total_changes, before)
        self.assertEqual(self.writer.db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)

    def test_batch_limit_does_not_skip_unread_samples(self):
        for _ in range(1001):
            self.writer.sample(FRAME)
        generation = self.writer.session_id
        first = snapshot(self.path, after=0, generation=generation)
        self.assertEqual(first["processed"], 1000)
        self.assertTrue(first["more"])
        second = snapshot(self.path, after=first["cursor"], generation=generation)
        self.assertEqual(second["processed"], 1)
        self.assertEqual(second["points"][0]["id"], 1001)
        self.assertFalse(second["more"])


class AreaTests(unittest.TestCase):
    def test_packaged_city_and_half_mile_buffer(self):
        area = area_overlay("la-habra-heights")
        self.assertEqual(area["provenance"]["geoid"], "0639304")
        self.assertEqual(area["provenance"]["buffer_meters"], 804.672)
        self.assertEqual(area["provenance"]["boundary_year"], 2022)
        kinds = {f["properties"]["kind"]: f for f in area["features"]}
        for feature in kinds.values():
            self.assertEqual(feature["geometry"]["type"], "Polygon")
            ring = feature["geometry"]["coordinates"][0]
            self.assertEqual(ring[0], ring[-1])
            self.assertTrue(all(-118.1 < lon < -117.8 and 33.8 < lat < 34.1 for lon, lat in ring))
        def bounds(feature):
            ring = feature["geometry"]["coordinates"][0]
            return min(p[0] for p in ring), min(p[1] for p in ring), max(p[0] for p in ring), max(p[1] for p in ring)
        city, buffer = bounds(kinds["city"]), bounds(kinds["buffer"])
        self.assertTrue(buffer[0] < city[0] and buffer[1] < city[1] and buffer[2] > city[2] and buffer[3] > city[3])
        self.assertIsNone(area_overlay("none"))

    def test_serve_defaults(self):
        args = parser().parse_args(["serve", "--db", "log.sqlite"])
        self.assertEqual((args.host, args.port, args.refresh, args.source, args.area),
                         ("127.0.0.1", 8765, 2, "live", "la-habra-heights"))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "live.sqlite"
        self.writer = Store(self.path, "mobile", "mobile", "btech_bluetooth", {})
        self.addCleanup(self.writer.close)
        self.server = make_server(self.path, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def get(self, route):
        with urlopen(self.base + route, timeout=3) as response:
            return response.read(), response.headers

    def test_real_http_server_follows_appended_data(self):
        first = json.loads(self.get("/api/snapshot")[0])
        self.writer.sample(FRAME)
        second = json.loads(self.get(f"/api/snapshot?after=0&generation={first['generation']}")[0])
        self.assertEqual(len(second["points"]), 1)
        self.assertEqual(second["cursor"], 1)
        self.assertEqual(self.get("/api/snapshot")[1]["Cache-Control"], "no-store")

    def test_html_assets_config_and_missing_route(self):
        self.assertIn(b"Live mobile reception", self.get("/")[0])
        self.assertIn(b"function poll", self.get("/assets/dashboard.js")[0])
        self.assertIn(b"Leaflet 1.9.4", self.get("/assets/leaflet.js")[0])
        self.assertIn(b".leaflet", self.get("/assets/leaflet.css")[0])
        self.assertEqual(self.get("/assets/images/layers.png")[1]["Content-Type"], "image/png")
        self.assertEqual(json.loads(self.get("/api/config")[0])["area"]["provenance"]["buffer_miles"], .5)
        with self.assertRaises(HTTPError) as error:
            self.get("/../pyproject.toml")
        self.assertEqual(error.exception.code, 404)

    def test_invalid_cursors_are_rejected(self):
        for cursor in ("-1", "bad", str(2**100)):
            with self.subTest(cursor=cursor), self.assertRaises(HTTPError) as error:
                self.get("/api/snapshot?after=" + cursor)
            self.assertEqual(error.exception.code, 400)

    def test_database_failure_returns_retryable_error(self):
        self.writer.db.execute("ALTER TABLE samples RENAME TO unavailable")
        self.writer.db.commit()
        with self.assertRaises(HTTPError) as error:
            self.get("/api/snapshot")
        self.assertEqual(error.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
