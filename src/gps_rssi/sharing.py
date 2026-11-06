"""Consistent portable snapshots and lossless, repeatable multi-station merging."""

from contextlib import closing
from pathlib import Path
import sqlite3

from .storage import SCHEMA


def readonly(path):
    return sqlite3.connect(Path(path).expanduser().resolve().as_uri() + "?mode=ro", uri=True)


def check_log(db):
    """Check the supported recording shape and relational integrity before sharing."""
    for table, columns in (
        ("sessions", "id, started_at, station, role, source, metadata_json"),
        ("samples", "id, session_id, received_at, direction, rssi_value, rssi_unit, "
                    "is_in_tx, is_in_rx, squelch_open, channel_id, gps_locked, latitude, longitude, position_time, raw_json"),
        ("events", "id, session_id, received_at, kind, detail_json"),
    ):
        expected = columns.split(", ")
        actual = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        if actual != expected:
            raise ValueError(f"Unsupported GPS-RSSI schema in {table}")
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("Database integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Database contains broken recording references")


def snapshot(path, output):
    """Use SQLite backup, including committed WAL data, without pausing a recorder."""
    output = Path(output).expanduser().resolve()
    # Exclusive creation also protects input aliases and existing destinations.
    with output.open("xb"):
        pass
    try:
        with closing(readonly(path)) as source, closing(sqlite3.connect(output)) as target:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            check_log(target)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output


def merge_databases(paths, output):
    """Create a new recording DB; identity is session UUID plus original row ID.

    Local integer IDs are remapped, never used to identify another radio. Origin
    tables survive snapshots and further merges, so overlapping and nested bundles
    deduplicate exactly. Conflicting copies of the same identity fail the merge.
    Every input is read in one transaction, including its provenance tables.
    """
    paths = [Path(p).expanduser().resolve() for p in paths]
    if not paths:
        raise ValueError("Supply at least one database")
    output = Path(output).expanduser().resolve()
    if output in paths and not output.exists():
        raise ValueError("Output cannot also be an input database")
    with output.open("xb"):
        pass
    counts = dict(sessions=0, samples=0, events=0, duplicate_samples=0, duplicate_events=0)
    try:
        with closing(sqlite3.connect(output)) as target:
            target.execute("PRAGMA foreign_keys=ON")
            target.executescript(SCHEMA)
            for table in ("samples", "events"):
                target.execute(f"""CREATE TABLE {table}_origins (
                    local_id INTEGER PRIMARY KEY REFERENCES {table}(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    original_id INTEGER NOT NULL, UNIQUE(session_id, original_id))""")
            target.commit()
            target.execute("BEGIN IMMEDIATE")
            for path in paths:
                with closing(readonly(path)) as source:
                    source.execute("BEGIN")
                    check_log(source)
                    for row in source.execute("SELECT * FROM sessions ORDER BY started_at, id"):
                        previous = target.execute("SELECT * FROM sessions WHERE id=?", (row[0],)).fetchone()
                        if previous is None:
                            target.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)", row)
                            counts["sessions"] += 1
                        elif previous != row:
                            raise ValueError(f"Conflicting session {row[0]} in {path.name}")
                    for table in ("samples", "events"):
                        has_origins = source.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table + "_origins",)).fetchone()
                        if has_origins:
                            orphan = source.execute(f"""SELECT 1 FROM {table}_origins o
                                JOIN {table} r ON r.id=o.local_id
                                WHERE o.session_id<>r.session_id OR o.original_id<=0 LIMIT 1""").fetchone()
                            if orphan:
                                raise ValueError(f"Invalid {table} provenance in {path.name}")
                        query = (f"SELECT r.*, COALESCE(o.original_id,r.id) FROM {table} r "
                                 f"LEFT JOIN {table}_origins o ON o.local_id=r.id ORDER BY r.id" if has_origins else
                                 f"SELECT r.*, r.id FROM {table} r ORDER BY r.id")
                        for record in source.execute(query):
                            values, original_id = record[1:-1], record[-1]
                            session_id = values[0]
                            previous = target.execute(f"""SELECT r.* FROM {table} r
                                JOIN {table}_origins o ON r.id=o.local_id
                                WHERE o.session_id=? AND o.original_id=?""", (session_id, original_id)).fetchone()
                            if previous is not None:
                                if previous[1:] != values:
                                    raise ValueError(f"Conflicting {table} identity {session_id}/{original_id} in {path.name}")
                                counts["duplicate_" + table] += 1
                                continue
                            marks = ",".join("?" for _ in values)
                            cursor = target.execute(f"INSERT INTO {table} VALUES (NULL,{marks})", values)
                            target.execute(f"INSERT INTO {table}_origins VALUES (?, ?, ?)",
                                           (cursor.lastrowid, session_id, original_id))
                            counts[table] += 1
            target.commit()
            check_log(target)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return counts
