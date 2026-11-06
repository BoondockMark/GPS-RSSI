import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gps_rssi.app import run_together
from gps_rssi.settings import load_settings
from gps_rssi.controls import Controls
from gps_rssi.dashboard import make_server
from gps_rssi.storage import Store
from test_analysis import FRAME
from test_app import LiveFakeConnection


class ResetStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "radio.sqlite"
        self.store = Store(self.path, "mobile", "mobile", "benlink_bluetooth", {"note": "keep"})
        self.addCleanup(self.store.close)
        self.store.sample(FRAME)
        self.store.event("original", {})

    def test_wal_backup_preserves_all_stations_and_new_session_is_empty(self):
        other = Store(self.path, "other", "mobile", "replay", {})
        other.sample(FRAME)
        other.close()
        old_id = self.store.session_id
        self.assertTrue(Path(str(self.path) + "-wal").exists())
        backup = self.store.reset()
        self.assertEqual(backup.parent, self.path.parent / "backups")
        with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT count(*) FROM samples").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT kind FROM events").fetchone()[0], "original")
        self.assertFalse(Path(str(backup) + "-wal").exists())
        self.assertNotEqual(old_id, self.store.session_id)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT station, metadata_json FROM sessions").fetchone(),
                         ("mobile", '{"note": "keep"}'))
        self.assertEqual(self.store.db.execute("SELECT kind FROM events").fetchone()[0], "database_reset")
        self.store.sample(FRAME)
        self.assertEqual(self.store.db.execute("SELECT id,session_id FROM samples").fetchone(), (1, self.store.session_id))

    def test_active_reader_finishes_old_snapshot_without_file_replacement(self):
        with closing(sqlite3.connect(self.path)) as reader:
            reader.execute("BEGIN")
            self.assertEqual(reader.execute("SELECT count(*) FROM samples").fetchone()[0], 1)
            self.store.reset()
            self.assertEqual(reader.execute("SELECT count(*) FROM samples").fetchone()[0], 1)
            reader.commit()
            self.assertEqual(reader.execute("SELECT count(*) FROM samples").fetchone()[0], 0)

    def test_backup_creation_failure_keeps_data_and_session(self):
        blocked = self.path.parent / "backups"
        blocked.write_text("preserve this existing file")
        old_id = self.store.session_id
        with self.assertRaises(OSError):
            self.store.reset()
        self.assertEqual(self.store.session_id, old_id)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)
        self.assertEqual(blocked.read_text(), "preserve this existing file")
        self.assertFalse(self.store.db.in_transaction)
        self.store.sample(FRAME)

    def test_clear_failure_rolls_back_but_keeps_verified_backup(self):
        self.store.db.execute("CREATE TRIGGER reject_clear BEFORE DELETE ON samples BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        old_id = self.store.session_id
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.reset()
        self.assertEqual(self.store.session_id, old_id)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT kind FROM events").fetchone()[0], "original")
        self.assertEqual(len(list((self.path.parent / "backups").glob("*.sqlite"))), 1)
        self.assertFalse(self.store.db.in_transaction)

    def test_repeated_resets_never_overwrite_backups(self):
        first = self.store.reset()
        first_bytes = first.read_bytes()
        second = self.store.reset()
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), first_bytes)

    def test_name_collision_never_deletes_the_existing_backup(self):
        original_open = Path.open

        def collision(path, mode="r", *args, **kwargs):
            if mode == "xb":
                with original_open(path, "wb") as existing:
                    existing.write(b"existing backup - preserve")
                raise FileExistsError("simulated collision")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", collision), self.assertRaises(FileExistsError):
            self.store.reset()
        backup = next((self.path.parent / "backups").glob("*.sqlite"))
        self.assertEqual(backup.read_bytes(), b"existing backup - preserve")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)


class ControlIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.settings = load_settings(self.path / "mobile.toml")
        self.settings["recording"]["interval"] = .02
        self.store = Store(self.path / "radio.sqlite", "mobile", "mobile", "benlink_bluetooth", {})
        self.server = make_server(self.store.path, port=0)
        self.controls = Controls(asyncio.get_running_loop())
        self.connections = []

        def factory():
            conn = LiveFakeConnection()
            self.connections.append(conn)
            return conn

        self.task = asyncio.create_task(run_together(self.store, factory, self.server, self.settings,
                                                    browser=False, controls=self.controls))
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        await self.wait_sample()

    async def asyncTearDown(self):
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.server.server_close()
        self.store.close()
        self.temp.cleanup()

    async def request(self, path, data=None, token=True, content_type="application/json"):
        def send():
            headers = {"Content-Type": content_type}
            if token:
                headers["X-GPS-RSSI-Control"] = self.controls.token
            request = Request(self.base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
            try:
                with urlopen(request, timeout=3) as response:
                    return response.status, json.load(response)
            except HTTPError as exc:
                with exc:
                    return exc.code, json.load(exc)
        return await asyncio.to_thread(send)

    async def wait_sample(self):
        for _ in range(200):
            if self.store.db.execute("SELECT count(*) FROM samples WHERE session_id=?", (self.store.session_id,)).fetchone()[0]:
                return
            await asyncio.sleep(.005)
        self.fail("No sample received")

    async def wait_operation(self, operation):
        for _ in range(200):
            code, state = await self.request("/api/control?id=" + operation["id"])
            self.assertEqual(code, 200)
            if state["state"] in ("completed", "failed"):
                return state
            await asyncio.sleep(.005)
        self.fail("Control operation did not finish")

    async def test_exit_via_http_disconnects_and_stops_both_services(self):
        count = self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0]
        code, operation = await self.request("/api/control", {"action": "exit"})
        self.assertEqual(code, 202)
        result = await self.wait_operation(operation)
        self.assertEqual(result["state"], "completed")
        await asyncio.wait_for(self.task, 3)
        self.assertTrue(self.connections[0].disconnected)
        self.assertEqual(self.server.fileno(), -1)
        self.assertGreaterEqual(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], count)
        reasons = [json.loads(r[0]).get("reason") for r in self.store.db.execute("SELECT detail_json FROM events WHERE kind='stopped'")]
        self.assertIn("dashboard_exit", reasons)

    async def test_exit_finishes_even_if_browser_does_not_read_final_status(self):
        code, _ = await self.request("/api/control", {"action": "exit"})
        self.assertEqual(code, 202)
        await asyncio.wait_for(self.task, 4)
        self.assertTrue(self.connections[0].disconnected)
        self.assertEqual(self.server.fileno(), -1)

    async def test_running_response_does_not_acknowledge_concurrent_completion(self):
        self.controls.operation = {"id": "race", "action": "exit", "state": "running"}
        original_status = self.controls.status

        def completes_after_snapshot():
            response = original_status()
            self.controls.update("completed", "Stopped")
            return response

        handler_finished = asyncio.Event()
        original_finish = self.server.RequestHandlerClass.finish

        def finish_request(handler):
            try:
                original_finish(handler)
            finally:
                self.controls.loop.call_soon_threadsafe(handler_finished.set)

        with patch.object(self.controls, "status", side_effect=completes_after_snapshot), \
             patch.object(self.server.RequestHandlerClass, "finish", finish_request):
            code, response = await self.request("/api/control?id=race")
            await asyncio.wait_for(handler_finished.wait(), 1)
        self.assertEqual((code, response["state"]), (200, "running"))
        self.assertFalse(self.controls.ack.is_set())
        code, response = await self.request("/api/control?id=race")
        self.assertEqual(response["state"], "completed")
        await asyncio.wait_for(self.controls.ack.wait(), 1)
        self.assertTrue(self.controls.ack.is_set())

    async def test_keyboard_exit_during_disconnect_does_not_run_pending_reset(self):
        release, entered = asyncio.Event(), asyncio.Event()
        conn = self.connections[0]
        original_disconnect = conn.disconnect
        old_session = self.store.session_id

        async def delayed_disconnect():
            entered.set()
            await release.wait()
            await original_disconnect()

        conn.disconnect = delayed_disconnect
        await self.request("/api/control", {"action": "reset", "confirm": True})
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.task.cancel()
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.task, 3)
        self.assertEqual(self.store.session_id, old_session)
        self.assertFalse((self.path / "backups").exists())
        self.assertTrue(conn.disconnected)

    async def test_reset_via_http_backs_up_disconnects_and_resumes(self):
        old_id = self.store.session_id
        code, operation = await self.request("/api/control", {"action": "reset", "confirm": True})
        self.assertEqual(code, 202)
        result = await self.wait_operation(operation)
        self.assertEqual(result["state"], "completed")
        self.assertTrue(self.connections[0].disconnected)
        self.assertNotEqual(old_id, self.store.session_id)
        await self.wait_sample()
        self.assertGreaterEqual(len(self.connections), 2)
        with closing(sqlite3.connect(result["backup"])) as backup:
            self.assertGreater(backup.execute("SELECT count(*) FROM samples").fetchone()[0], 0)
            self.assertEqual(backup.execute("SELECT id FROM sessions").fetchone()[0], old_id)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        _, snapshot = await self.request("/api/snapshot?after=999999&generation=" + old_id)
        self.assertTrue(snapshot["reset"])
        self.assertEqual(snapshot["generation"], self.store.session_id)
        self.assertFalse(self.task.done())

    async def test_failed_backup_preserves_records_and_resumes_original_session(self):
        old_id = self.store.session_id
        (self.path / "backups").write_text("blocked")
        _, operation = await self.request("/api/control", {"action": "reset", "confirm": True})
        result = await self.wait_operation(operation)
        self.assertEqual(result["state"], "failed")
        self.assertIn("original data retained", result["message"])
        self.assertEqual(self.store.session_id, old_id)
        self.assertGreater(self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0], 0)
        for _ in range(100):
            if len(self.connections) > 1:
                break
            await asyncio.sleep(.005)
        self.assertGreater(len(self.connections), 1)
        self.assertFalse(self.task.done())

    async def test_requests_require_token_json_and_reset_confirmation(self):
        for token, payload, content_type, expected in (
            (False, {"action": "exit"}, "application/json", 403),
            (True, {"action": "reset"}, "application/json", 400),
            (True, {"action": "reset", "confirm": False}, "application/json", 400),
            (True, {"action": "exit"}, "text/plain", 400),
            (True, {"action": "invalid"}, "application/json", 400),
        ):
            code, _ = await self.request("/api/control", payload, token=token, content_type=content_type)
            self.assertEqual(code, expected)
        self.assertEqual(self.controls.status()["state"], "idle")
        code, config = await self.request("/api/config")
        self.assertTrue(config["controls"]["enabled"])
        self.assertEqual(config["controls"]["token"], self.controls.token)

    async def test_duplicate_operations_are_rejected_while_radio_is_disconnecting(self):
        release, entered = asyncio.Event(), asyncio.Event()
        conn = self.connections[0]
        original = conn.disconnect

        async def delayed_disconnect():
            entered.set()
            await release.wait()
            await original()

        conn.disconnect = delayed_disconnect
        _, operation = await self.request("/api/control", {"action": "reset", "confirm": True})
        try:
            await asyncio.wait_for(entered.wait(), 1)
            code, _ = await self.request("/api/control", {"action": "exit"})
            self.assertEqual(code, 409)
        finally:
            release.set()
        self.assertEqual((await self.wait_operation(operation))["state"], "completed")


if __name__ == "__main__":
    unittest.main()
