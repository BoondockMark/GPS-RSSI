"""Report calculations and text formatting over already-loaded measurements."""

from collections import Counter, defaultdict
from datetime import timezone
import json

from .measurements import finite, gps_problem, timestamp


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
