"""Read-only result quality summaries; planned tone gaps are not link failures."""

from contextlib import closing

from .measurements import timestamp
from .sharing import readonly


def result_quality(database, operations=(), source="live"):
    quality = dict(sample_count=0, first_sample_at=None, last_sample_at=None, duration_seconds=None,
        mobile_receive_samples=0, gps_points=0, gps_coverage_percent=None,
        connection_interruptions=0, recovered_interruptions=0, unrecovered_interruptions=0,
        recovered_gap_seconds=0, failed_operations=sum(op.get("state") in ("failed", "interrupted") for op in operations))
    if database is None:
        return quality
    where = "" if source == "all" else " WHERE s.source='replay'" if source == "replay" else " WHERE s.source<>'replay'"
    with closing(readonly(database)) as db:
        db.execute("BEGIN")
        row = db.execute("""SELECT count(*), min(julianday(m.received_at)), max(julianday(m.received_at)),
            coalesce(sum(s.role='mobile' AND m.is_in_rx=1 AND m.is_in_tx=0),0)
            FROM samples m JOIN sessions s ON s.id=m.session_id""" + where).fetchone()
        quality["sample_count"], first, last, quality["mobile_receive_samples"] = row
        if first is not None and last is not None:
            date_query = "SELECT m.received_at FROM samples m JOIN sessions s ON s.id=m.session_id" + where
            date_query += (" AND " if where else " WHERE ") + "julianday(m.received_at) IS NOT NULL ORDER BY julianday(m.received_at)"
            first_at = db.execute(date_query + " ASC LIMIT 1").fetchone()[0]
            last_at = db.execute(date_query + " DESC LIMIT 1").fetchone()[0]
            quality.update(first_sample_at=first_at, last_sample_at=last_at,
                           duration_seconds=round(max(0, (last-first)*86400), 3))
        pending = {}
        events = db.execute("""SELECT e.session_id,e.kind,e.received_at FROM events e JOIN sessions s ON s.id=e.session_id"""
            + where + (" AND " if where else " WHERE ")
            + "e.kind IN ('connection_error','connected') ORDER BY e.session_id,julianday(e.received_at),e.id")
        for recording, kind, at in events:
            if kind == "connection_error":
                quality["connection_interruptions"] += 1
                pending.setdefault(recording, at)
            elif recording in pending:
                started = pending.pop(recording)
                quality["recovered_interruptions"] += 1
                try:
                    quality["recovered_gap_seconds"] += max(0, (timestamp(at)-timestamp(started)).total_seconds())
                except (ValueError, TypeError, AttributeError):
                    pass
        quality["unrecovered_interruptions"] = len(pending)
    quality["recovered_gap_seconds"] = round(quality["recovered_gap_seconds"], 3)
    return quality


def with_gps_coverage(quality, mapped_points):
    quality = dict(quality, gps_points=mapped_points)
    denominator = quality["mobile_receive_samples"]
    quality["gps_coverage_percent"] = round(100 * mapped_points / denominator, 1) if denominator else None
    return quality
