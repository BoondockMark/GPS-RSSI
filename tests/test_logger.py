import asyncio
import copy
import csv
import json
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gps_rssi.btech import collect
from gps_rssi.cli import parser, run
from gps_rssi.storage import Store


FRAME = {
    "status": {"rssi": 100 * 8 / 15, "is_in_tx": False, "is_in_rx": True,
               "is_sq": True, "is_gps_locked": True, "curr_ch_id": 1},
    "position": {"latitude": 33.96, "longitude": -117.95,
                 "time": "2026-11-06T00:00:00+00:00", "accuracy": 10},
}


class Model:
    def __init__(self, values):
        self.values = values

    def __getattr__(self, name):
        return self.values[name]

    def model_dump(self, **kwargs):
        return self.values.copy()


class FakeConnection:
    """Only exposes read methods so accidental setter/PTT calls fail tests."""
    def __init__(self, *, status_error=None, gps_error=None):
        self.status_error = status_error
        self.gps_error = gps_error
        self.disconnected = False
        self.calls = []

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.disconnected = True

    async def get_device_info(self):
        self.calls.append("get_device_info")
        return Model({"vendor_id": 1})

    async def get_status(self):
        self.calls.append("get_status")
        if self.status_error:
            raise self.status_error
        return Model(FRAME["status"])

    async def get_position(self):
        self.calls.append("get_position")
        if self.gps_error:
            raise self.gps_error
        return Model(FRAME["position"])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "radio.sqlite"
        self.store = Store(self.path, "mobile-test", "mobile", "replay", {})
        self.addCleanup(self.store.close)

    def test_strength_and_units_survive_reopening(self):
        self.store.sample(FRAME)
        with sqlite3.connect(self.path) as other:
            row = other.execute("SELECT direction,rssi_value,rssi_unit,latitude,raw_json FROM samples").fetchone()
        self.assertEqual(row[:4], ("downlink", FRAME["status"]["rssi"], "benshi_relative_0_100", 33.96))
        self.assertEqual(json.loads(row[4]), FRAME)

    def test_transmit_is_not_a_receive_measurement(self):
        frame = copy.deepcopy(FRAME)
        frame["status"]["is_in_tx"] = True
        self.store.sample(frame)
        self.assertEqual(self.store.db.execute("SELECT direction FROM samples").fetchone()[0], "unqualified")

    def test_lost_fix_does_not_reuse_previous_coordinates(self):
        self.store.sample(FRAME)
        frame = copy.deepcopy(FRAME)
        frame["status"]["is_gps_locked"] = False
        self.store.sample(frame)
        self.assertEqual(self.store.db.execute("SELECT latitude,longitude FROM samples ORDER BY id DESC").fetchone(), (None, None))

    def test_bad_input_does_not_create_sample(self):
        for bad in (float("nan"), -1, 101, True):
            frame = copy.deepcopy(FRAME)
            frame["status"]["rssi"] = bad
            with self.assertRaises(ValueError):
                self.store.sample(frame)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 0)

    def test_repeater_receive_is_uplink(self):
        other = Store(self.path, "repeater-test", "repeater", "replay", {})
        try:
            other.sample(FRAME)
            self.assertEqual(other.db.execute("SELECT direction FROM samples").fetchone()[0], "uplink")
        finally:
            other.close()

    def test_replay_and_csv_export_keep_source(self):
        source = Path(self.temp.name) / "frames.jsonl"
        source.write_text(json.dumps(FRAME) + "\n")
        run(parser().parse_args(["replay", str(source), "--db", str(self.path), "--station", "demo"]))
        output = Path(self.temp.name) / "samples.csv"
        run(parser().parse_args(["export", "--db", str(self.path), "--output", str(output)]))
        with output.open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "replay")
        self.assertEqual(rows[0]["station"], "demo")
        with self.assertRaises(FileExistsError):
            run(parser().parse_args(["export", "--db", str(self.path), "--output", str(self.path)]))
        self.assertEqual(self.store.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_error_in_replay_reports_line_and_keeps_prior_sample(self):
        source = Path(self.temp.name) / "bad.jsonl"
        source.write_text(json.dumps(FRAME) + "\n{}\n")
        with self.assertRaisesRegex(ValueError, r"bad.jsonl:2"):
            run(parser().parse_args(["replay", str(source), "--db", str(self.path), "--station", "demo"]))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)


class AcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "radio.sqlite", "test", "mobile", "test", {})
        self.addCleanup(self.store.close)

    async def test_polling_uses_only_getters_and_preserves_gps_time(self):
        conn = FakeConnection()
        count = await collect(self.store, lambda: conn, gps=True, max_samples=1)
        self.assertEqual(count, 1)
        self.assertEqual(conn.calls, ["connect", "get_device_info", "get_status", "get_position"])
        self.assertTrue(conn.disconnected)
        row = self.store.db.execute("SELECT position_time,raw_json,received_at FROM samples").fetchone()
        self.assertEqual(row[0], FRAME["position"]["time"])
        self.assertEqual(json.loads(row[1])["status_received_at"], row[2])

    async def test_reconnect_uses_new_connection_after_status_timeout(self):
        first, second = FakeConnection(status_error=TimeoutError()), FakeConnection()
        connections = iter([first, second])
        await collect(self.store, lambda: next(connections), reconnect_delay=.001, max_samples=1)
        self.assertTrue(first.disconnected and second.disconnected)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='connection_error'").fetchone()[0], 1)

    async def test_backend_specific_exception_also_reconnects(self):
        class BackendError(Exception):
            pass
        first, second = FakeConnection(status_error=BackendError("link lost")), FakeConnection()
        connections = iter([first, second])
        await collect(self.store, lambda: next(connections), reconnect_delay=.001, max_samples=1)
        self.assertTrue(first.disconnected and second.disconnected)

    async def test_gps_timeout_keeps_strength_without_inventing_location(self):
        conn = FakeConnection(gps_error=TimeoutError())
        await collect(self.store, lambda: conn, gps=True, max_samples=1)
        row = self.store.db.execute("SELECT rssi_value,latitude FROM samples").fetchone()
        self.assertEqual(row, (FRAME["status"]["rssi"], None))
        self.assertTrue(conn.disconnected)

    async def test_gps_not_requested_unless_enabled(self):
        conn = FakeConnection()
        await collect(self.store, lambda: conn, max_samples=1)
        self.assertNotIn("get_position", conn.calls)

    async def test_cancellation_disconnects_and_keeps_completed_samples(self):
        conn = FakeConnection()
        task = asyncio.create_task(collect(self.store, lambda: conn, interval=100))
        while self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0] == 0:
            await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(conn.disconnected)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)


@unittest.skipUnless(importlib.util.find_spec("benlink"), "optional Bluetooth dependency not installed")
class BenlinkCompatibilityTests(unittest.TestCase):
    def test_pinned_dependency_has_required_read_api(self):
        from benlink.command import CommandConnection
        for name in ("new_ble", "new_rfcomm", "connect", "disconnect", "get_device_info", "get_status", "get_position"):
            self.assertTrue(callable(getattr(CommandConnection, name, None)), name)

    def test_real_status_model_preserves_relative_strength(self):
        from benlink.command import Status
        model = Status(is_power_on=True, is_in_tx=False, is_sq=True, is_in_rx=True,
                       double_channel="OFF", is_scan=False, is_radio=False,
                       curr_ch_id=1, is_gps_locked=True, is_hfp_connected=False,
                       is_aoc_connected=False, rssi=100 * 8 / 15, curr_region=0)
        self.assertEqual(model.model_dump(mode="json")["rssi"], FRAME["status"]["rssi"])


if __name__ == "__main__":
    unittest.main()
