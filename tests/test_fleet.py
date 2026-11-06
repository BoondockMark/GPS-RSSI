from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from gps_rssi.cli import parser
from gps_rssi.fleet import start_fleet, stop_recorders, validate_fleet


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def config(self, name, *, address=None, station=None, database=None, port=8765):
        path = self.root / (name + ".toml")
        path.write_text(f'''[radio]
address = "{address or name}"
[recording]
station = "{station or name}"
database = "{database or name + '.sqlite'}"
[dashboard]
port = {port}
''')
        return path

    def test_multiple_transceivers_have_independent_resolved_resources(self):
        configs = validate_fleet([self.config("a"), self.config("b", port=8766)])
        self.assertEqual([s["recording"]["station"] for _, s in configs], ["a", "b"])
        self.assertEqual(configs[0][1]["recording"]["database"], str(self.root / "a.sqlite"))
        self.assertEqual(parser().parse_args(["fleet", "--config", "a.toml", "--config", "b.toml"]).config,
                         [Path("a.toml"), Path("b.toml")])

    def test_conflicts_fail_before_any_recorder_starts(self):
        first = self.config("a")
        for key, kwargs in (("address", dict(address="A", port=8766)),
                            ("database", dict(database="a.sqlite", port=8766)),
                            ("station", dict(station="a", port=8766)), ("port", dict(port=8765))):
            with self.subTest(key=key), patch("gps_rssi.fleet.subprocess.Popen") as popen:
                with self.assertRaisesRegex(ValueError, key):
                    start_fleet([first, self.config("b", **kwargs)])
                popen.assert_not_called()

    def test_missing_profiles_are_not_bootstrapped_to_the_same_radio(self):
        path = self.root / "missing.toml"
        with self.assertRaisesRegex(ValueError, "Create"):
            validate_fleet([path])
        self.assertFalse(path.exists())

    @unittest.skipUnless(hasattr(os, "link"), "Hard links unavailable")
    def test_database_hardlink_alias_is_rejected(self):
        first = self.config("a")
        with closing(sqlite3.connect(self.root / "a.sqlite")) as db:
            db.execute("CREATE TABLE test(x)")
        try:
            os.link(self.root / "a.sqlite", self.root / "b.sqlite")
        except OSError:
            self.skipTest("Filesystem does not support hard links")
        with self.assertRaisesRegex(ValueError, "separate database"):
            validate_fleet([first, self.config("b", port=8766)])

    def test_child_failure_stops_other_recorders(self):
        processes = [Mock(pid=123, returncode=None), Mock(pid=456, returncode=1)]
        processes[0].poll.return_value = None
        processes[1].poll.return_value = 1
        with patch("gps_rssi.fleet.subprocess.Popen", side_effect=processes) as popen, \
             patch("gps_rssi.fleet.stop_recorders") as stop:
            with self.assertRaisesRegex(RuntimeError, "status 1"):
                start_fleet([self.config("a"), self.config("b", port=8766)], no_browser=True)
            stop.assert_called_once_with(processes)
            self.assertIn("--no-browser", popen.call_args.args[0])
            self.assertEqual(popen.call_args.args[0][:4], [sys.executable, "-m", "gps_rssi.cli", "start"])

    def test_partial_launch_failure_cleans_up_started_children(self):
        process = Mock()
        with patch("gps_rssi.fleet.subprocess.Popen", side_effect=[process, OSError("launch failed")]), \
             patch("gps_rssi.fleet.stop_recorders") as stop:
            with self.assertRaisesRegex(OSError, "launch failed"):
                start_fleet([self.config("a"), self.config("b", port=8766)])
            stop.assert_called_once_with([process])

    @unittest.skipIf(os.name == "nt", "POSIX signal integration test")
    def test_stop_waits_for_real_child_cleanup(self):
        command = [sys.executable, "-u", "-c", """
import signal,sys,time
def stop(*args):
    print('saved and disconnected', flush=True)
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
print('ready', flush=True)
time.sleep(60)
"""]
        process = subprocess.Popen(command, start_new_session=True, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            stop_recorders([process], grace=2)
            self.assertEqual(process.returncode, 0)
            self.assertIn("saved and disconnected", process.stdout.read())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
