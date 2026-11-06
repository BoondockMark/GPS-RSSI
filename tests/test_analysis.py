from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gps_rssi.analysis import load_log, make_report
from gps_rssi.cli import parser, run
from gps_rssi.mapping import render_map, select_points
from gps_rssi.storage import Store


FRAME = {
    "status_received_at": "2026-11-06T00:00:01+00:00",
    "status": {"rssi": 0, "is_in_tx": False, "is_in_rx": True,
               "is_sq": False, "is_gps_locked": True, "curr_ch_id": 1},
    "position": {"latitude": 33.9608, "longitude": -117.9506,
                 "time": "2026-11-06T00:00:00+00:00", "accuracy": 10},
}


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "log.sqlite"

    def session(self, *, station="mobile", source="btech_bluetooth", role="mobile", frames=()):
        store = Store(self.db, station, role, source, {})
        try:
            for frame in frames:
                store.sample(frame)
            return store.session_id
        finally:
            store.close()

    def point_result(self, **changes):
        session = {"id": "one", "station": "mobile", "source": "btech_bluetooth", "role": "mobile"}
        sample = {"id": 1, "session_id": "one", "received_at": FRAME["status_received_at"],
                  "direction": "downlink", "is_in_rx": 1, "is_in_tx": 0, "gps_locked": 1,
                  "latitude": 33.96, "longitude": -117.95, "position_time": FRAME["position"]["time"],
                  "raw_json": json.dumps(FRAME), "rssi_unit": "benshi_relative_0_100",
                  "rssi_value": 0, "squelch_open": 0, "channel_id": 1}
        sample.update(changes)
        return select_points([session], [sample])

    def test_old_survey_time_is_not_compared_with_today(self):
        points, excluded = self.point_result()
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["rssi"], 0)
        self.assertFalse(points[0]["squelch_open"])
        self.assertEqual(excluded, {})

    def test_gps_rejection_reasons(self):
        cases = [
            ({"gps_locked": 0}, "no_gps_lock"),
            ({"latitude": None}, "missing_coordinates"),
            ({"latitude": 91}, "invalid_coordinates"),
            ({"longitude": float("nan")}, "invalid_coordinates"),
            ({"position_time": None}, "invalid_timestamp"),
            ({"position_time": "2026-11-06T00:00:00"}, "invalid_timestamp"),
            ({"position_time": "2026-11-05T23:59:00Z"}, "stale_or_clock_skewed_fix"),
            ({"position_time": "2026-11-06T00:01:00Z"}, "stale_or_clock_skewed_fix"),
            ({"raw_json": "{}"}, "missing_accuracy"),
            ({"raw_json": '{"position":{"accuracy":101}}'}, "poor_accuracy"),
            ({"raw_json": '{"position":{"accuracy":-1}}'}, "invalid_accuracy"),
            ({"raw_json": '{"position":{"accuracy":true}}'}, "invalid_accuracy"),
        ]
        for changes, reason in cases:
            with self.subTest(reason=reason, changes=changes):
                points, excluded = self.point_result(**changes)
                self.assertEqual(points, [])
                self.assertEqual(excluded, {reason: 1})

    def test_time_zone_offsets_are_respected(self):
        points, excluded = self.point_result(position_time="2026-11-05T16:00:00-08:00")
        self.assertEqual(len(points), 1)
        self.assertEqual(excluded, {})

    def test_tx_idle_and_wrong_units_are_not_plotted(self):
        for changes, reason in [({"is_in_tx": 1}, "not_downlink_receive"),
                                ({"is_in_rx": 0}, "not_downlink_receive"),
                                ({"direction": "uplink"}, "not_downlink_receive"),
                                ({"rssi_unit": "volts"}, "unsupported_rssi"),
                                ({"rssi_value": 101}, "unsupported_rssi")]:
            with self.subTest(changes=changes):
                self.assertEqual(self.point_result(**changes), ([], {reason: 1}))

    def test_repeater_role_is_excluded(self):
        self.session(role="repeater", frames=[FRAME])
        sessions, samples, _ = load_log(self.db)
        self.assertEqual(select_points(sessions, samples), ([], {"not_mobile": 1}))

    def test_source_station_and_session_filters(self):
        live = self.session(frames=[FRAME])
        replay = self.session(station="synthetic", source="replay", frames=[FRAME])
        sessions, samples, _ = load_log(self.db, source="live")
        self.assertEqual([s["id"] for s in sessions], [live])
        self.assertEqual(len(samples), 1)
        self.assertEqual(load_log(self.db, source="replay")[0][0]["id"], replay)
        self.assertEqual(load_log(self.db, station="synthetic", session=live)[1], [])
        self.assertEqual(load_log(self.db, station="' OR 1=1 --")[0], [])

    def test_empty_sessions_and_error_events_are_reported(self):
        session = self.session()
        with sqlite3.connect(self.db) as db:
            for kind, detail in [("connection_error", {"stage": "connect"}),
                                 ("connection_error", {}), ("disconnect_error", {})]:
                db.execute("INSERT INTO events VALUES (NULL, ?, ?, ?, ?)",
                           (session, FRAME["status_received_at"], kind, json.dumps(detail)))
        report = make_report(*load_log(self.db))
        self.assertEqual(report["sample_count"], 0)
        self.assertEqual(report["sessions"][0]["samples"], 0)
        self.assertEqual(report["sample_time_range"], {"first": None, "last": None})
        self.assertEqual(report["connection_error_stages"], {"connect": 1, "unspecified": 1})
        self.assertEqual(report["event_counts"]["disconnect_error"], 1)

    def test_report_receive_stats_exclude_tx_and_count_gps_quality(self):
        tx = copy.deepcopy(FRAME)
        tx["status"].update(is_in_tx=True, rssi=100)
        no_gps = copy.deepcopy(FRAME)
        no_gps["position"] = None
        no_gps["status"].update(is_gps_locked=False, rssi=20)
        self.session(frames=[FRAME, tx, no_gps])
        report = make_report(*load_log(self.db))
        self.assertEqual(report["sample_count"], 3)
        self.assertEqual(report["receive_rssi"]["benshi_relative_0_100"],
                         {"count": 2, "min": 0, "max": 20, "mean": 10})
        self.assertEqual(report["gps_counts"], {"usable": 2, "no_gps_lock": 1})

    def test_cli_json_report_and_live_only_map(self):
        self.session(frames=[FRAME])
        self.session(source="replay", frames=[FRAME])
        before = self.db.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            run(parser().parse_args(["report", "--db", str(self.db), "--json"]))
        self.assertEqual(json.loads(output.getvalue())["sample_count"], 2)
        target = Path(self.temp.name) / "map.html"
        with redirect_stdout(io.StringIO()):
            run(parser().parse_args(["map", "--db", str(self.db), "--output", str(target)]))
        page = target.read_text()
        payload = page.split('<script id="measurement-data" type="application/json">')[1].split('</script>')[0]
        points = json.loads(payload)["points"]
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["source"], "btech_bluetooth")
        self.assertEqual(self.db.read_bytes(), before)
        with self.assertRaises(FileExistsError):
            run(parser().parse_args(["map", "--db", str(self.db), "--output", str(target)]))

    def test_map_keeps_replay_identity_and_escapes_html(self):
        station = '</script><script>alert("bad")</script>'
        self.session(station=station, source="replay", frames=[FRAME])
        sessions, samples, _ = load_log(self.db, source="all")
        points, excluded = select_points(sessions, samples)
        page = render_map(points, excluded, max_age=10, max_accuracy=100)
        self.assertNotIn(station, page)
        payload = page.split('<script id="measurement-data" type="application/json">')[1].split('</script>')[0]
        decoded = json.loads(payload)
        self.assertEqual(decoded["points"][0]["station"], station)
        self.assertEqual(decoded["points"][0]["source"], "replay")

    def test_explicit_all_source_map_includes_both_sources(self):
        self.session(frames=[FRAME])
        self.session(source="replay", frames=[FRAME])
        target = Path(self.temp.name) / "both.html"
        with redirect_stdout(io.StringIO()):
            run(parser().parse_args(["map", "--db", str(self.db), "--source", "all", "--output", str(target)]))
        payload = target.read_text().split('<script id="measurement-data" type="application/json">')[1].split('</script>')[0]
        self.assertEqual({p["source"] for p in json.loads(payload)["points"]}, {"replay", "btech_bluetooth"})

    def test_report_orders_timestamps_by_instant_not_text(self):
        first = copy.deepcopy(FRAME)
        first["status_received_at"] = "2026-11-06T00:00:00+01:00"
        self.session(frames=[FRAME, first])
        report = make_report(*load_log(self.db))
        self.assertEqual(report["sample_time_range"]["first"], "2026-11-05T23:00:00+00:00")
        self.assertEqual(report["sample_time_range"]["last"], FRAME["status_received_at"])

    def test_missing_database_is_not_created(self):
        with self.assertRaises(sqlite3.OperationalError):
            load_log(self.db)
        self.assertFalse(self.db.exists())

    def test_quality_thresholds_can_be_adjusted(self):
        self.session(frames=[FRAME])
        sessions, samples, _ = load_log(self.db)
        samples[0]["position_time"] = "2026-11-05T23:59:40Z"
        self.assertEqual(len(select_points(sessions, samples)[0]), 0)
        self.assertEqual(len(select_points(sessions, samples, max_age=30)[0]), 1)
        self.assertEqual(len(select_points(sessions, samples, max_age=30, max_accuracy=5)[0]), 0)


if __name__ == "__main__":
    unittest.main()
