import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import os
import subprocess
import threading

from gps_rssi.storage import Store
from gps_rssi.workspace import Workspace
from gps_rssi.workspace_jobs import Jobs


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.a = self.workspace.save_profile(dict(name="Vehicle 1", address="AA:BB:CC:DD:EE:01", role="mobile", label="Roof antenna", gps=True))
        self.b = self.workspace.save_profile(dict(name="Monitor", address="AA:BB:CC:DD:EE:02", role="fixed", label="Fixed", gps=False))

    def test_profiles_get_distinct_defaults_and_edits_preserve_unrelated_settings(self):
        self.assertNotEqual(self.a["port"], self.b["port"])
        path = self.root / self.a["id"]
        original = path.read_text().replace("interval = 1.0", "interval = 2.5 # preserve this comment")
        path.write_text(original)
        fresh = self.workspace.profile(self.a["id"])
        updated = self.workspace.save_profile({**fresh, "name": "Car one", "label": "New notes"})
        self.assertEqual(updated["port"], fresh["port"])
        self.assertIn("interval = 2.5 # preserve this comment", path.read_text())
        self.assertTrue(list((self.root / "setup-backups").glob("*.bak")))
        self.assertEqual(self.workspace.profile(self.b["id"])["role"], "fixed")

    def test_stale_edits_conflicts_and_external_paths_are_rejected(self):
        with self.assertRaises(RuntimeError):
            self.workspace.save_profile({**self.a, "revision": "old"})
        for data in ({**self.a, "address": self.b["address"]}, {**self.a, "port": self.b["port"]},
                     {**self.a, "database": "../outside.sqlite"}, {**self.a, "database": "data/workspace/results.sqlite"}):
            with self.subTest(data=data), self.assertRaises((ValueError, RuntimeError)):
                self.workspace.save_profile(data)
        with self.assertRaises(ValueError):
            self.workspace.save_profile(dict(name="Duplicate", address=self.a["address"].replace(":", "-"), role="mobile"))

    def test_active_radio_setup_cannot_be_edited(self):
        self.workspace.jobs.entries["busy"] = dict(state="running", resources=["radio:" + self.a["address"].casefold()])
        with self.assertRaisesRegex(RuntimeError, "already in use"):
            self.workspace.save_profile(self.a)

    def test_transmission_requires_one_use_review_and_keeps_reviewed_parameters(self):
        with patch.object(self.workspace.jobs, "launch", return_value={"id": "fake"}) as launch:
            for data in (dict(kind="transmit"), dict(kind="transmit", confirmed=True, intent="unknown")):
                with self.assertRaises(ValueError):
                    self.workspace.launch(data)
            launch.assert_not_called()
            review = self.workspace.prepare_transmit(dict(profile=self.a["id"], frequency=1000, duration=2, level_dbfs=-18))
            launch.assert_not_called()  # Reviewing never connects or transmits.
            self.workspace.launch(dict(kind="transmit", intent=review["intent"], confirmed=True, frequency=2000, duration=9))
            command = launch.call_args.args[1](self.root / "result")
            self.assertIn("--transmit", command)
            self.assertEqual(command[command.index("--frequency") + 1], "1000")
            self.assertEqual(command[command.index("--duration") + 1], "2")
            with self.assertRaises(ValueError):
                self.workspace.launch(dict(kind="transmit", intent=review["intent"], confirmed=True))

    def test_expired_or_changed_setup_invalidates_transmission_review(self):
        review = self.workspace.prepare_transmit(dict(profile=self.a["id"]))
        self.workspace.intents[review["intent"]]["expires"] = time.monotonic() - 1
        with self.assertRaises(ValueError):
            self.workspace.launch(dict(kind="transmit", intent=review["intent"], confirmed=True))
        review = self.workspace.prepare_transmit(dict(profile=self.a["id"]))
        self.workspace.save_profile({**self.a, "label": "Changed"})
        with self.assertRaisesRegex(RuntimeError, "setup changed"):
            self.workspace.launch(dict(kind="transmit", intent=review["intent"], confirmed=True))

    def test_recording_and_audio_share_radio_resources_but_other_radios_are_independent(self):
        with patch.object(self.workspace.jobs, "launch", return_value={"id": "fake"}) as launch:
            self.workspace.launch(dict(kind="record", profile=self.a["id"]))
            record_resources = launch.call_args.kwargs["resources"]
            self.workspace.launch(dict(kind="capture", profile=self.a["id"], duration=30))
            capture_resources = launch.call_args.kwargs["resources"]
            self.assertTrue(set(record_resources) & set(capture_resources))
            self.workspace.launch(dict(kind="capture", profile=self.b["id"], duration=30))
            self.assertFalse(set(capture_resources) & set(launch.call_args.kwargs["resources"]))

    def test_previews_never_get_a_transmit_argument_or_radio_resource(self):
        with patch.object(self.workspace.jobs, "launch", return_value={"id": "fake"}) as launch:
            self.workspace.launch(dict(kind="preview", frequency=1000, duration=1))
            command = launch.call_args.args[1](self.root / "result")
            self.assertNotIn("--transmit", command)
            self.assertNotIn("--address", command)
            self.assertNotIn("resources", launch.call_args.kwargs)

    def test_dataset_catalog_never_accepts_arbitrary_client_paths(self):
        for kind, payload in (("export", dict(database="../../secret")), ("analyze", dict(audio="/etc/passwd")),
                              ("survey", dict(databases=["/tmp/log.sqlite"]))):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.workspace.launch(dict(kind=kind, **payload))

    def test_imports_validate_files_and_use_generated_names(self):
        source = self.root / "original.sqlite"
        store = Store(source, "imported", "mobile", "replay", {})
        store.close()
        before = source.read_bytes()
        temporary = self.root / "upload"
        shutil.copy2(source, temporary)
        datasets = self.workspace.register_import(temporary, "database", "../../shared.sqlite")
        imported = next(d for d in datasets["databases"] if d["source"] == "import")
        self.assertEqual(imported["name"], "shared.sqlite")
        self.assertTrue(self.workspace.dataset(imported["id"]).is_relative_to(self.root))
        self.assertEqual(source.read_bytes(), before)
        invalid = self.root / "invalid"
        invalid.write_bytes(b"not a recording")
        with self.assertRaises(Exception):
            self.workspace.register_import(invalid, "database", "bad.sqlite")


class WorkspaceJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.jobs = Jobs(Path(self.temp.name))

    def test_restart_keeps_unknown_radio_operations_blocked_until_acknowledged(self):
        folder = self.jobs.directory / ("a" * 32)
        folder.mkdir()
        entry = dict(id=folder.name, kind="transmit", title="Tone", state="running", resources=["radio:abc"],
                     created_at="2026-11-06T17:00:00Z", profile="radio.toml")
        (folder / "operation.json").write_text(json.dumps(entry))
        restored = Jobs(self.jobs.root)
        self.assertEqual(restored.entries[folder.name]["state"], "unknown")
        with self.assertRaises(RuntimeError):
            restored.check_resources(["radio:abc"])
        with self.assertRaises(ValueError):
            restored.acknowledge(folder.name, False)
        restored.acknowledge(folder.name, True)
        restored.check_resources(["radio:abc"])

    def test_unverified_transmitter_stop_blocks_new_use(self):
        key = "b" * 32
        folder = self.jobs.directory / key
        (folder / "result").mkdir(parents=True)
        entry = dict(id=key, kind="transmit", title="Tone", profile="a.toml", state="running", resources=["radio:a"],
            created_at="2026-11-06T17:00:00Z", forced=False, stop_requested=False)
        self.jobs.entries[key] = entry
        (folder / "result/report.json").write_text(json.dumps(dict(transmission_requested=True, stop_attempted=True, stop_verified=False)))
        process = Mock()
        process.wait.return_value = 1
        self.jobs._watch(key, process)
        self.assertEqual(entry["state"], "unknown")
        with self.assertRaises(RuntimeError):
            self.jobs.check_resources(["radio:a"])

    def test_crash_with_only_in_progress_transmit_report_keeps_radio_reserved(self):
        key = "c" * 32
        folder = self.jobs.directory / key
        (folder / "result").mkdir(parents=True)
        self.jobs.entries[key] = dict(id=key, kind="transmit", title="Tone", profile="a.toml", state="running",
            resources=["radio:a"], created_at="2026-11-06T17:00:00Z", forced=False, stop_requested=False)
        (folder / "result/report.json").write_text(json.dumps(dict(state="transmitting", transmission_requested=True,
            transmission_started_at="2026-11-06T17:00:01Z", stop_attempted=False, stop_verified=None)))
        process = Mock()
        process.wait.return_value = 1
        self.jobs._watch(key, process)
        self.assertEqual(self.jobs.entries[key]["state"], "unknown")
        with self.assertRaises(RuntimeError):
            self.jobs.check_resources(["radio:a"])

    def test_owned_running_process_cannot_be_acknowledged_as_stopped(self):
        key = "d" * 32
        self.jobs.entries[key] = dict(state="unknown")
        process = Mock()
        process.poll.return_value = None
        self.jobs.processes[key] = process
        with self.assertRaisesRegex(RuntimeError, "still running"):
            self.jobs.acknowledge(key, True)

    def test_commands_are_argument_lists_and_launch_failure_is_saved(self):
        with patch("gps_rssi.workspace_jobs.subprocess.Popen", side_effect=OSError("launch failed")) as popen:
            with self.assertRaisesRegex(OSError, "launch failed"):
                self.jobs.launch("scan", lambda _: ["scan", "--json"], title="Find radios")
        self.assertEqual(popen.call_args.args[0][-2:], ["scan", "--json"])
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(self.jobs.list()[0]["state"], "failed")

    def test_stop_requests_graceful_signal_and_keeps_result_until_process_finishes(self):
        class Process:
            pid = 123456
            def __init__(self):
                self.done = threading.Event()
                self.send_signal = Mock()
                self.returncode = None
            def wait(self, timeout=None):
                if not self.done.wait(timeout):
                    raise subprocess.TimeoutExpired("fake", timeout)
                return self.returncode
            def poll(self):
                return self.returncode
        process = Process()
        with patch("gps_rssi.workspace_jobs.subprocess.Popen", return_value=process), \
             patch("gps_rssi.workspace_jobs.os.killpg", create=True) as signal_group:
            job = self.jobs.launch("record", lambda _: ["start"], title="Recording", resources=["radio:a"])
            self.jobs.stop(job["id"])
            self.assertEqual(self.jobs.entries[job["id"]]["state"], "stopping")
            if os.name == "nt":
                process.send_signal.assert_called_once()
            else:
                signal_group.assert_called_once()
            process.returncode = 0
            process.done.set()
            deadline = time.monotonic() + 2
            while self.jobs.entries[job["id"]]["state"] == "stopping" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.jobs.describe(job["id"])["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
