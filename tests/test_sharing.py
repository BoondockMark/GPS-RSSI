from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gps_rssi.sharing import merge_databases, snapshot
from gps_rssi.storage import Store, load_log


def frame(value=0, *, gps=True, rx=True, tx=False):
    return dict(status_received_at="2026-11-06T17:00:00Z",
                status=dict(rssi=value, is_in_tx=tx, is_in_rx=rx, is_sq=False, is_gps_locked=gps, curr_ch_id=1),
                position=dict(latitude=33.96, longitude=-117.95, time="2026-11-06T17:00:00Z", accuracy=8) if gps else None)


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a, self.b = self.root / "a.sqlite", self.root / "b.sqlite"
        self.writer = Store(self.a, "mobile-a", "mobile", "benlink_bluetooth", {"label": "UV-PRO"})
        self.addCleanup(self.writer.close)
        self.writer.sample(frame(0))
        self.writer.event("connected", {"radio": "a"})
        other = Store(self.b, "mobile-b", "mobile", "benlink_bluetooth", {"label": "UV-50PRO"})
        other.sample(frame(90))
        other.event("connected", {"radio": "b"})
        other.close()

    def test_snapshot_includes_committed_wal_and_is_standalone(self):
        target = self.root / "shared.sqlite"
        snapshot(self.a, target)
        self.assertEqual(load_log(target), load_log(self.a))
        self.writer.sample(frame(20))
        self.assertEqual(len(load_log(target)[1]), 1)
        self.assertFalse(target.with_name(target.name + "-wal").exists())
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_numeric_id_collisions_do_not_mix_radios(self):
        output = self.root / "merged.sqlite"
        counts = merge_databases([self.a, self.b], output)
        self.assertEqual(counts["samples"], 2)
        sessions, samples, events = load_log(output)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(len(events), 2)
        self.assertEqual({r["rssi_value"] for r in samples}, {0, 90})
        self.assertEqual(len({r["id"] for r in samples}), 2)
        with closing(sqlite3.connect(output)) as db:
            self.assertEqual(db.execute("SELECT original_id FROM samples_origins ORDER BY local_id").fetchall(), [(1,), (1,)])

    def test_nested_and_overlapping_merges_deduplicate_samples_and_events(self):
        first, second = self.root / "first.sqlite", self.root / "second.sqlite"
        merge_databases([self.b, self.a], first)  # A now has a remapped local ID.
        counts = merge_databases([first, self.a, self.b, first], second)
        self.assertEqual(counts, dict(sessions=2, samples=2, events=2, duplicate_samples=4, duplicate_events=4))
        self.assertEqual(len(load_log(second)[1]), 2)

    def test_newer_snapshot_adds_only_new_observations(self):
        before = self.root / "before.sqlite"
        snapshot(self.a, before)
        self.writer.sample(frame(33))
        self.writer.event("disconnected", {})
        output = self.root / "combined.sqlite"
        counts = merge_databases([before, self.a], output)
        self.assertEqual(counts["samples"], 2)
        self.assertEqual(counts["duplicate_samples"], 1)
        self.assertEqual(counts["events"], 2)
        self.assertEqual([r["rssi_value"] for r in load_log(output)[1]], [0, 33])

    def test_conflicting_samples_sessions_and_events_fail_without_output(self):
        for table, assignment in (("samples", "rssi_value=99"), ("sessions", "station='different'"),
                                  ("events", "kind='different'")):
            with self.subTest(table=table):
                conflict, output = self.root / f"{table}.sqlite", self.root / f"failed-{table}.sqlite"
                snapshot(self.a, conflict)
                with closing(sqlite3.connect(conflict)) as db, db:
                    db.execute(f"UPDATE {table} SET {assignment}")
                with self.assertRaisesRegex(ValueError, "Conflicting"):
                    merge_databases([self.a, conflict], output)
                self.assertFalse(output.exists())

    def test_inputs_and_existing_outputs_are_never_overwritten(self):
        before = self.b.read_bytes()
        for operation in (lambda: snapshot(self.a, self.b), lambda: merge_databases([self.a], self.b),
                          lambda: snapshot(self.a, self.a)):
            with self.assertRaises(FileExistsError):
                operation()
        merge_databases([self.b], self.root / "new.sqlite")
        self.assertEqual(self.b.read_bytes(), before)

    def test_invalid_input_missing_input_and_broken_provenance_fail_cleanly(self):
        invalid = self.root / "invalid.sqlite"
        with closing(sqlite3.connect(invalid)) as db, db:
            db.execute("CREATE TABLE nonsense(x)")
        for source in (invalid, self.root / "missing.sqlite"):
            for operation in (snapshot, lambda a, b: merge_databases([a], b)):
                output = self.root / "failure.sqlite"
                with self.assertRaises((ValueError, sqlite3.Error)):
                    operation(source, output)
                self.assertFalse(output.exists())
        first = self.root / "first.sqlite"
        merge_databases([self.a], first)
        with closing(sqlite3.connect(first)) as db, db:
            db.execute("UPDATE samples_origins SET session_id='missing'")
        with self.assertRaisesRegex(ValueError, "references"):
            merge_databases([first], self.root / "bad-origin.sqlite")

    def test_merged_database_can_record_and_reset_with_provenance(self):
        output = self.root / "working.sqlite"
        merge_databases([self.a, self.b], output)
        store = Store(output, "next-run", "mobile", "benlink_bluetooth", {})
        try:
            store.sample(frame())
            backup = store.reset()
            self.assertTrue(backup.exists())
            self.assertEqual(load_log(output)[1], [])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
