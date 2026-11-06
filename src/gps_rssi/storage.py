"""SQLite recording, consistent reads, CSV export, and transactional reset."""

import csv
import json
import math
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .measurements import utc_now


def validate_frame(frame):
    status = frame["status"]
    for name in ("is_in_tx", "is_in_rx", "is_sq", "is_gps_locked"):
        if not isinstance(status[name], bool):
            raise ValueError(f"status.{name} must be a boolean")
    strength = status["rssi"]
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 100:
        raise ValueError("rssi must be a finite Benlink value from 0 to 100")
    if type(status["curr_ch_id"]) is not int or status["curr_ch_id"] < 0:
        raise ValueError("curr_ch_id must be a nonnegative integer")
    if frame.get("position") is not None:
        for key, limit in (("latitude", 90), ("longitude", 180)):
            value = frame["position"][key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -limit <= value <= limit:
                raise ValueError(f"invalid {key}")
        # Position time is preserved separately from host receipt time.
        position_time = datetime.fromisoformat(frame["position"]["time"].replace("Z", "+00:00"))
        if position_time.utcoffset() is None:
            raise ValueError("position.time must include its UTC offset")


class Store:
    def __init__(self, path, station, role, source, metadata):
        if role not in ("mobile", "fixed"):
            raise ValueError("Recording role must be mobile or fixed")
        self.path = Path(path).resolve()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                station TEXT NOT NULL, role TEXT NOT NULL, source TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                received_at TEXT NOT NULL, direction TEXT NOT NULL,
                rssi_value REAL NOT NULL, rssi_unit TEXT NOT NULL,
                is_in_tx INTEGER NOT NULL, is_in_rx INTEGER NOT NULL,
                squelch_open INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                gps_locked INTEGER NOT NULL, latitude REAL, longitude REAL,
                position_time TEXT, raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                received_at TEXT NOT NULL, kind TEXT NOT NULL, detail_json TEXT NOT NULL
            );
        """)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.session_id = str(uuid.uuid4())
        self.role = role
        self.db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                        (self.session_id, utc_now(), station, role, source, json.dumps(metadata)))
        self.db.commit()

    def event(self, kind, detail):
        self.db.execute("INSERT INTO events VALUES (NULL, ?, ?, ?, ?)",
                        (self.session_id, utc_now(), kind, json.dumps(detail)))
        self.db.commit()

    def sample(self, frame):
        validate_frame(frame)
        s = frame["status"]
        # Never carry forward an old position or attach an unlocked fix to a map.
        p = (frame.get("position") or {}) if s["is_gps_locked"] else {}
        direction = "unqualified"
        if s["is_in_rx"] and not s["is_in_tx"]:
            direction = "downlink" if self.role == "mobile" else "uplink"
        self.db.execute("INSERT INTO samples VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            self.session_id, frame.get("status_received_at", utc_now()), direction, s["rssi"], "benshi_relative_0_100",
            s["is_in_tx"], s["is_in_rx"], s["is_sq"], s["curr_ch_id"], s["is_gps_locked"],
            p.get("latitude"), p.get("longitude"), p.get("time"), json.dumps(frame, allow_nan=False)))
        self.db.commit()

    def close(self):
        self.db.close()

    def reset(self):
        """Back up a quiescent writer, then atomically start an empty session.

        The collector must be stopped first. A reserved write transaction also
        prevents other SQLite writers changing data between backup and reset.
        Readers can finish their snapshots; no database/WAL files are replaced.
        """
        backup = None
        created = False
        verified = False
        self.db.execute("BEGIN IMMEDIATE")
        try:
            session = self.db.execute(
                "SELECT station, role, source, metadata_json FROM sessions WHERE id=?",
                (self.session_id,)).fetchone()
            if session is None:
                raise ValueError("Current recording session is missing; reset canceled")
            directory = self.path.parent / "backups"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = directory / f"{self.path.stem}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite"
            with backup.open("xb"):
                pass
            created = True
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source, \
                 closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
                destination.execute("PRAGMA journal_mode=DELETE")
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("Backup verification failed; reset canceled")
            verified = True
            new_id = str(uuid.uuid4())
            self.db.execute("DELETE FROM samples")
            self.db.execute("DELETE FROM events")
            self.db.execute("DELETE FROM sessions")
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                            (new_id, utc_now(), *session))
            self.db.execute("INSERT INTO events VALUES (NULL, ?, ?, ?, ?)",
                            (new_id, utc_now(), "database_reset", json.dumps({"backup": str(backup)})))
            self.db.commit()
            self.session_id = new_id
            return backup
        except BaseException:
            self.db.rollback()
            if created and not verified:
                backup.unlink(missing_ok=True)
            raise


def load_log(path, *, station=None, session=None, source="all", after_id=None,
             sample_limit=None, event_limit=None, newest=False):
    clauses, values = [], []
    for column, value in (("station", station), ("id", session)):
        if value is not None:
            clauses.append(f"{column} = ?")
            values.append(value)
    if source != "all":
        clauses.append("source = 'replay'" if source == "replay" else "source <> 'replay'")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")  # Consistent view even if the logger is still running.
        sessions = [dict(r) for r in db.execute("SELECT * FROM sessions" + where + " ORDER BY started_at, id", values)]
        # Join against the selected sessions in SQL rather than expanding a large IN list.
        selected = "SELECT id FROM sessions" + where
        sample_sql = f"SELECT * FROM samples WHERE session_id IN ({selected})"
        sample_values = list(values)
        if after_id is not None:
            sample_sql += " AND id > ?"
            sample_values.append(after_id)
        sample_sql += " ORDER BY id " + ("DESC" if newest else "ASC")
        if sample_limit is not None:
            sample_sql += " LIMIT ?"
            sample_values.append(sample_limit)
        samples = [dict(r) for r in db.execute(sample_sql, sample_values)]
        if newest:
            samples.reverse()
        event_sql = f"SELECT * FROM events WHERE session_id IN ({selected}) ORDER BY id"
        event_values = list(values)
        if event_limit is not None:
            event_sql += " DESC LIMIT ?"
            event_values.append(event_limit)
        events = [dict(r) for r in db.execute(event_sql, event_values)]
        if event_limit is not None:
            events.reverse()
    return sessions, samples, events


def export_csv(path, output):
    """Export samples with explicit connection lifetime and no overwriting."""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("""SELECT s.station, s.role, s.source, m.*
            FROM samples m JOIN sessions s ON s.id=m.session_id ORDER BY m.id""")
        with output.open("x", newline="", encoding="utf-8") as target:
            writer = csv.writer(target)
            writer.writerow([column[0] for column in rows.description])
            writer.writerows(rows)
