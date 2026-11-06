"""Read-only summaries and GPS qualification for existing logger databases."""

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import sqlite3


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


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timestamp lacks UTC offset")
    return result


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def gps_problem(sample, max_age=10, max_accuracy=100):
    """Compare fix time with measurement time, not time of viewing the log."""
    if not sample["gps_locked"]:
        return "no_gps_lock"
    lat, lon = sample["latitude"], sample["longitude"]
    if lat is None or lon is None:
        return "missing_coordinates"
    if not finite(lat) or not finite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return "invalid_coordinates"
    try:
        difference = abs((timestamp(sample["received_at"]) - timestamp(sample["position_time"])).total_seconds())
    except (ValueError, TypeError, AttributeError, OverflowError):
        return "invalid_timestamp"
    if difference > max_age:
        return "stale_or_clock_skewed_fix"
    try:
        accuracy = json.loads(sample["raw_json"])["position"]["accuracy"]
    except (ValueError, TypeError, KeyError):
        return "missing_accuracy"
    if not finite(accuracy) or accuracy < 0:
        return "invalid_accuracy"
    if accuracy > max_accuracy:
        return "poor_accuracy"
    return None


def make_report(sessions, samples, events, *, max_age=10, max_accuracy=100):
    session_counts = Counter(s["session_id"] for s in samples)
    sample_times = []
    for sample in samples:
        try:
            sample_times.append(timestamp(sample["received_at"]).astimezone(timezone.utc))
        except (ValueError, TypeError, AttributeError, OverflowError):
            pass
    gps = Counter(gps_problem(s, max_age, max_accuracy) or "usable" for s in samples)
    strengths = defaultdict(list)
    for s in samples:
        if s["is_in_rx"] and not s["is_in_tx"] and finite(s["rssi_value"]):
            strengths[s["rssi_unit"]].append(s["rssi_value"])
    stages = Counter()
    for event in events:
        if event["kind"] == "connection_error":
            try:
                detail = json.loads(event["detail_json"])
                stage = detail.get("stage", "unspecified") if isinstance(detail, dict) else "unspecified"
            except (ValueError, TypeError):
                stage = "unspecified"
            stages[str(stage)] += 1
    return {
        "sessions": [{"id": s["id"], "station": s["station"], "role": s["role"],
                      "source": s["source"], "started_at": s["started_at"],
                      "samples": session_counts[s["id"]]} for s in sessions],
        "sample_count": len(samples),
        "sample_time_range": {"first": min(sample_times).isoformat() if sample_times else None,
                              "last": max(sample_times).isoformat() if sample_times else None},
        "invalid_sample_timestamps": len(samples) - len(sample_times),
        "state_counts": {"receive": sum(bool(s["is_in_rx"] and not s["is_in_tx"]) for s in samples),
                         "transmit": sum(bool(s["is_in_tx"]) for s in samples),
                         "squelch_open": sum(bool(s["squelch_open"]) for s in samples),
                         "gps_locked": sum(bool(s["gps_locked"]) for s in samples)},
        "receive_rssi": {unit: {"count": len(v), "min": min(v), "max": max(v),
                                "mean": sum(v) / len(v)} for unit, v in strengths.items()},
        "gps_counts": dict(gps),
        "gps_limits": {"max_time_difference_seconds": max_age, "max_accuracy_meters": max_accuracy},
        "event_counts": dict(Counter(e["kind"] for e in events)),
        "connection_error_stages": dict(stages),
    }


def format_report(report):
    lines = [f"Sessions: {len(report['sessions'])}", f"Samples: {report['sample_count']}"]
    times = report["sample_time_range"]
    lines.append(f"Sample times: {times['first'] or 'none'} to {times['last'] or 'none'}")
    if report["invalid_sample_timestamps"]:
        lines.append(f"Invalid sample timestamps: {report['invalid_sample_timestamps']}")
    for s in report["sessions"]:
        lines.append(f"  {s['id']} | {s['station']} | {s['role']} | {s['source']} | {s['samples']} samples")
    lines.append("Radio states (counts may overlap):")
    lines.extend(f"  {name}: {count}" for name, count in report["state_counts"].items())
    lines.append("Receive RSSI (excludes transmit samples):")
    for unit, stats in report["receive_rssi"].items():
        lines.append(f"  {unit}: n={stats['count']}, min={stats['min']:.1f}, "
                     f"max={stats['max']:.1f}, mean={stats['mean']:.1f}")
    if not report["receive_rssi"]:
        lines.append("  No receive measurements.")
    lines.append("GPS quality (all selected samples):")
    lines.extend(f"  {reason}: {count}" for reason, count in sorted(report["gps_counts"].items()))
    limits = report["gps_limits"]
    lines.append(f"  Limits: time difference <= {limits['max_time_difference_seconds']:g}s; "
                 f"reported accuracy <= {limits['max_accuracy_meters']:g}m")
    lines.append("Events (GPS and connection errors may describe the same failure):")
    lines.extend(f"  {kind}: {count}" for kind, count in sorted(report["event_counts"].items()))
    if not report["event_counts"]:
        lines.append("  None.")
    lines.append("Connection errors by stage:")
    lines.extend(f"  {stage}: {count}" for stage, count in sorted(report["connection_error_stages"].items()))
    if not report["connection_error_stages"]:
        lines.append("  None.")
    lines.append("RSSI is relative, not dBm. GPS quality does not establish usable voice or two-way coverage.")
    return "\n".join(lines)
