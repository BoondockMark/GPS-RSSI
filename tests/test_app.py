from contextlib import closing
import asyncio
import json
from importlib.resources import files
from pathlib import Path
import sqlite3
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from gps_rssi.app import run_together, start
from gps_rssi.settings import load_settings
from gps_rssi.locking import InstanceLock
from gps_rssi.cli import parser
from gps_rssi.dashboard import make_server
from gps_rssi.storage import Store
from test_analysis import FRAME
from test_logger import FakeConnection, Model
from gps_rssi.measurements import utc_now


class LiveFakeConnection(FakeConnection):
    async def get_position(self):
        self.calls.append("get_position")
        return Model({**FRAME["position"], "time": utc_now()})


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mobile.toml"

    def test_first_launch_creates_personal_defaults_and_resolves_paths(self):
        settings = load_settings(self.path)
        template = tomllib.loads(files("gps_rssi").joinpath("profiles/mobile.toml").read_text())
        self.assertEqual(settings["radio"]["address"], template["radio"]["address"])
        self.assertTrue(settings["recording"]["gps"])
        self.assertEqual(settings["recording"]["connect_timeout"], 60)
        self.assertEqual(settings["recording"]["database"], str(self.path.parent / "data/uvpro-gps.sqlite"))
        self.assertEqual(settings["dashboard"]["area"], "la-habra-heights")
        self.assertTrue(self.path.exists())

    def test_existing_settings_are_never_overwritten(self):
        content = '[radio]\naddress="my-other-radio"\n[recording]\ndatabase="other.sqlite"\n[dashboard]\nopen_browser=false\n'
        self.path.write_text(content)
        settings = load_settings(self.path)
        self.assertEqual(settings["radio"]["address"], "my-other-radio")
        self.assertEqual(settings["recording"]["database"], str(self.path.parent / "other.sqlite"))
        self.assertFalse(settings["dashboard"]["open_browser"])
        self.assertEqual(self.path.read_text(), content)

    def test_bad_settings_fail_before_starting_any_services(self):
        invalid = ['[radio]\nunknown="x"', '[dashboard]\nport=true',
                   '[recording]\ngps="yes"', '[dashboard]\nrefresh=nan',
                   '[radio]\ntransport="rfcomm"', '[dashboard]\narea="elsewhere"',
                   '[recording]\ndatabase=""', '[recording]\nrole="invalid"', '[dashboard]\nport=70000',
                   '[unknown]\nkey=1', '[radio]\naddress=12']
        for content in invalid:
            with self.subTest(content=content):
                self.path.write_text(content)
                with self.assertRaises(ValueError):
                    load_settings(self.path)

    def test_duplicate_lock_is_rejected_and_release_allows_restart(self):
        lock = self.path.parent / "data/app.lock"
        with InstanceLock(lock):
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                with InstanceLock(lock):
                    self.fail("Duplicate app acquired the lock")
        with InstanceLock(lock):
            pass

    def test_lock_is_released_after_failure(self):
        with self.assertRaises(ValueError):
            with InstanceLock(self.path):
                raise ValueError("test")
        with InstanceLock(self.path):
            pass

    def test_start_cli_is_short_and_has_explicit_browser_override(self):
        args = parser().parse_args(["start"])
        self.assertIsNone(args.config)
        self.assertEqual(args.profile, "mobile")
        self.assertFalse(args.no_browser)
        self.assertTrue(parser().parse_args(["start", "--no-browser"]).no_browser)

    def test_fixed_profile_has_separate_identity_role_database_and_no_gps_requirement(self):
        fixed = self.path.parent / "fixed.toml"
        settings = load_settings(fixed, profile="fixed")
        template = tomllib.loads(files("gps_rssi").joinpath("profiles/fixed.toml").read_text())
        self.assertEqual(settings["radio"]["address"], template["radio"]["address"])
        self.assertEqual(settings["recording"]["role"], "fixed")
        self.assertEqual(settings["recording"]["station"], "fixed-uv50pro")
        self.assertFalse(settings["recording"]["gps"])
        self.assertEqual(settings["recording"]["database"], str(fixed.parent / "data/uv50pro.sqlite"))
        self.assertEqual(settings["dashboard"]["port"], 8766)

    def test_existing_custom_fixed_address_is_authoritative(self):
        self.path.write_text('[radio]\naddress="custom-fixed-radio"\n[recording]\nrole="fixed"\n')
        before = self.path.read_bytes()
        settings = load_settings(self.path, profile="fixed")
        self.assertEqual(settings["radio"]["address"], "custom-fixed-radio")
        self.assertEqual(settings["recording"]["role"], "fixed")
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_mobile_config_inherits_mobile_role(self):
        self.path.write_text('[radio]\naddress="existing-mobile-radio"\n')
        settings = load_settings(self.path)
        self.assertEqual(settings["recording"]["role"], "mobile")
        self.assertEqual(settings["radio"]["address"], "existing-mobile-radio")


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.settings = load_settings(self.path / "mobile.toml")
        self.settings["recording"]["interval"] = .01
        self.store = Store(self.path / "samples.sqlite", "mobile-uvpro", "mobile", "btech_bluetooth", {})
        self.addCleanup(self.store.close)
        self.server = make_server(self.path / "samples.sqlite", port=0)
        self.addCleanup(self.server.server_close)

    async def wait_for_sample(self):
        for _ in range(200):
            if self.store.db.execute("SELECT count(*) FROM samples").fetchone()[0]:
                return
            await asyncio.sleep(.005)
        self.fail("Logger did not produce a sample")

    async def test_one_process_collects_and_serves_then_cleans_up_on_stop(self):
        conn = LiveFakeConnection()
        task = asyncio.create_task(run_together(self.store, lambda: conn, self.server, self.settings, browser=False))
        try:
            await self.wait_for_sample()
            def read():
                with urlopen(f"http://127.0.0.1:{self.server.server_port}/api/snapshot", timeout=3) as response:
                    return json.load(response)
            snapshot = await asyncio.to_thread(read)
            self.assertTrue(snapshot["points"])
            self.assertEqual(conn.calls[:4], ["connect", "get_device_info", "get_status", "get_position"])
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(conn.disconnected)
        self.assertEqual(self.server.fileno(), -1)
        self.assertFalse(any(t.name == "gps-rssi-dashboard" for t in threading.enumerate()))

    async def test_browser_failure_does_not_stop_logging(self):
        conn = FakeConnection()
        with patch("gps_rssi.app.webbrowser.open", side_effect=OSError("no browser")) as browser:
            task = asyncio.create_task(run_together(self.store, lambda: conn, self.server, self.settings))
            try:
                await self.wait_for_sample()
                browser.assert_called_once_with(f"http://127.0.0.1:{self.server.server_port}", new=2)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(conn.disconnected)

    async def test_logger_failure_stops_dashboard_and_disconnects(self):
        def fail():
            raise RuntimeError("radio adapter unavailable")
        with self.assertRaisesRegex(RuntimeError, "adapter unavailable"):
            await run_together(self.store, fail, self.server, self.settings, browser=False)
        self.assertEqual(self.server.fileno(), -1)

    async def test_dashboard_failure_cancels_logger(self):
        conn = FakeConnection()
        task = asyncio.create_task(run_together(self.store, lambda: conn, self.server, self.settings, browser=False))
        await self.wait_for_sample()
        await asyncio.to_thread(self.server.shutdown)
        with self.assertRaisesRegex(RuntimeError, "Dashboard stopped unexpectedly"):
            await task
        self.assertTrue(conn.disconnected)
        self.assertEqual(self.server.fileno(), -1)


class StartupTests(unittest.TestCase):
    def test_fixed_start_uses_setup_address_and_records_uplink_without_gps_queries(self):
        from gps_rssi.btech import collect as real_collect
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            config = path / "fixed.toml"
            settings = load_settings(config, profile="fixed")
            conn = LiveFakeConnection()

            async def one_sample(*args, **kwargs):
                return await real_collect(*args, max_samples=1, **kwargs)

            def bind_ephemeral(database, **options):
                options["port"] = 0
                return make_server(database, **options)

            with patch("gps_rssi.app.connection", return_value=conn) as connect, \
                 patch("gps_rssi.app.make_server", side_effect=bind_ephemeral), \
                 patch("gps_rssi.app.collect", side_effect=one_sample):
                start(config, profile="fixed", no_browser=True)
            self.assertTrue(all(call.args[0] == settings["radio"]["address"] for call in connect.call_args_list))
            self.assertNotIn("get_position", conn.calls)
            self.assertTrue(conn.disconnected)
            with closing(sqlite3.connect(path / "data/uv50pro.sqlite")) as db, db:
                self.assertEqual(db.execute("SELECT station,role FROM sessions").fetchone(), ("fixed-uv50pro", "fixed"))
                self.assertEqual(db.execute("SELECT direction,latitude,longitude FROM samples").fetchone(), ("uplink", None, None))

    def test_public_start_records_with_saved_settings_and_no_browser_override(self):
        from gps_rssi.btech import collect as real_collect
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            config = path / "mobile.toml"
            conn = LiveFakeConnection()

            async def one_sample(*args, **kwargs):
                return await real_collect(*args, max_samples=1, **kwargs)

            def bind_ephemeral(database, **options):
                options["port"] = 0
                return make_server(database, **options)

            with patch("gps_rssi.app.connection", return_value=conn), \
                 patch("gps_rssi.app.make_server", side_effect=bind_ephemeral), \
                 patch("gps_rssi.app.collect", side_effect=one_sample), \
                 patch("gps_rssi.app.webbrowser.open") as browser:
                start(config, no_browser=True)
                browser.assert_not_called()
            self.assertTrue(conn.disconnected)
            with closing(sqlite3.connect(path / "data/uvpro-gps.sqlite")) as db, db:
                self.assertEqual(db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT station FROM sessions").fetchone()[0], "mobile-uvpro")

    def test_occupied_port_fails_without_connecting_radio_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store = Store(path / "occupied.sqlite", "other", "mobile", "replay", {})
            self.addCleanup(store.close)
            occupied = make_server(path / "occupied.sqlite", port=0)
            self.addCleanup(occupied.server_close)
            config = path / "mobile.toml"
            config.write_text(f'[dashboard]\nport={occupied.server_port}\nopen_browser=false\n')
            conn = FakeConnection()
            with patch("gps_rssi.app.connection", return_value=conn):
                with self.assertRaisesRegex(RuntimeError, "Dashboard port .* is busy"):
                    start(config)
            self.assertEqual(conn.calls, [])
            database = path / "data/uvpro-gps.sqlite"
            with InstanceLock(database.with_name(database.name + ".app.lock")):
                pass
            with closing(sqlite3.connect(database)) as db, db:
                self.assertEqual(db.execute("SELECT count(*) FROM samples").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT kind FROM events").fetchone()[0], "error")


if __name__ == "__main__":
    unittest.main()
