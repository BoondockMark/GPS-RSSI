"""Pure, conservative time association of two station logs; no database I/O."""

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math

from .measurements import finite, gps_problem, sample_frequencies, timestamp


DEFAULTS = dict(mobile_offset=0.0, fixed_offset=0.0, max_gps_gap=2.0,
                max_gps_age=10.0, max_gps_accuracy=50.0, max_gap=3.0,
                max_request=1.0, transition_guard=0.25)
BREAK_EVENTS = {"connected", "disconnected", "connection_error", "disconnect_error", "stopped"}
RESULT_FIELDS = (
    "receiver", "direction", "receiver_station", "receiver_session", "receiver_sample", "receiver_label",
    "source", "original_time", "original_request_time", "corrected_time", "corrected_request_time",
    "corrected_reply_time", "receiver_offset", "transmitter_offset", "status_latency", "rssi", "rssi_unit",
    "squelch_open", "channel", "frequency_hz", "rx_frequency_hz", "tx_frequency_hz", "status", "reason", "lat", "lon",
    "gps_session", "gps_sample", "gps_time",
    "gps_gap", "gps_accuracy", "transmitter_session", "transmitter_samples", "tx_bracket_seconds",
    "tx_before_gap", "tx_after_gap",
)


def seconds(value):
    return timestamp(value).timestamp()


def iso(time):
    return datetime.fromtimestamp(time, timezone.utc).isoformat()


def select_station(log, role):
    sessions, samples, events = log
    sessions = [s for s in sessions if s["role"] == role]
    names = {s["station"] for s in sessions}
    if len(names) != 1:
        raise ValueError(f"Select exactly one {role} station/session; found {len(names)} stations")
    ids = {s["id"] for s in sessions}
    return sessions, [s for s in samples if s["session_id"] in ids], [e for e in events if e["session_id"] in ids]


def prepare(log, offset, options):
    """Segment in sample-ID order, rejecting sessions with backwards host time."""
    sessions, samples, events = log
    by_session, breaks, blocks = defaultdict(list), defaultdict(list), []
    for event in events:
        if event["kind"] in BREAK_EVENTS:
            try:
                breaks[event["session_id"]].append(seconds(event["received_at"]) + offset)
            except (ValueError, TypeError, AttributeError, OverflowError):
                # An unplaceable interruption makes this session unsafe to join.
                breaks[event["session_id"]].append(None)
    for sample in samples:
        row = {**sample, "timing_error": "", "block": None}
        try:
            row["end"] = seconds(sample["received_at"]) + offset
            raw = json.loads(sample["raw_json"])
            row["original_request_time"] = raw.get("status_requested_at")
            row["start"] = seconds(raw["status_requested_at"]) + offset
            iso(row["start"]), iso(row["end"])  # Reject corrections outside datetime's supported range.
            if row["start"] > row["end"]:
                row["timing_error"] = "invalid_status_interval"
            elif row["end"] - row["start"] > options["max_request"]:
                row["timing_error"] = "slow_status_request"
            row["time"] = (row["start"] + row["end"]) / 2
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
            row["timing_error"] = "missing_or_invalid_status_time"
            for key in ("start", "end", "time"):
                row.pop(key, None)
        by_session[row["session_id"]].append(row)
    for session in sessions:
        rows = sorted(by_session[session["id"]], key=lambda r: r["id"])
        event_times = breaks[session["id"]]
        unsafe = None in event_times
        event_times = sorted(t for t in event_times if t is not None)
        previous, block = None, None
        for row in rows:
            if previous and "end" in row and row["end"] <= previous["end"]:
                unsafe = True
            if row["timing_error"]:
                block, previous = None, row if "end" in row else None
                continue
            if previous and row["start"] < previous["end"]:
                unsafe = True
            interrupted = previous and bisect_right(event_times, row["end"]) > bisect_right(event_times, previous["end"])
            if block is None or interrupted or row["start"] - previous["end"] > options["max_gap"]:
                block = {"session": session, "rows": [], "start": row["start"], "end": row["end"]}
                blocks.append(block)
            block["rows"].append(row)
            block["end"] = row["end"]
            row["block"] = block
            previous = row
        if unsafe:
            for row in rows:
                row["timing_error"] = "clock_discontinuity_or_invalid_event_time"
                row["block"] = None
            blocks = [b for b in blocks if b["session"]["id"] != session["id"]]
    for block in blocks:
        block["starts"] = [r["start"] for r in block["rows"]]
        block["ends"] = [r["end"] for r in block["rows"]]
    return [r for rows in by_session.values() for r in rows], blocks


class BlockIndex:
    """Time lookup that explicitly rejects overlapping session/segment coverage."""
    def __init__(self, blocks):
        self.blocks = sorted(blocks, key=lambda b: b["start"])
        self.starts = [b["start"] for b in self.blocks]
        self.max_ends = []
        for block in self.blocks:
            self.max_ends.append(max(block["end"], self.max_ends[-1] if self.max_ends else -math.inf))

    def at(self, start, end):
        found = []
        index = bisect_right(self.starts, end) - 1
        while index >= 0 and self.max_ends[index] >= start:
            block = self.blocks[index]
            if block["end"] >= start:
                found.append(block)
            index -= 1
        if len(found) != 1:
            return None, "overlapping_sessions" if found else "outside_overlap_or_connection_gap"
        if found[0]["start"] > start or found[0]["end"] < end:
            return None, "status_interval_crosses_boundary"
        return found[0], ""


def gps_track(block, options, rejected):
    fixes = defaultdict(list)
    for row in block["rows"]:
        # Correct the host clock, never GPS UTC.
        corrected = {**row, "received_at": iso(row["end"])}
        reason = gps_problem(corrected, options["max_gps_age"], options["max_gps_accuracy"])
        if reason:
            rejected[reason] += 1
            continue
        fixes[seconds(row["position_time"])].append(row)
    entries = []
    for time, rows in sorted(fixes.items()):
        conflict = len({(r["latitude"], r["longitude"]) for r in rows}) != 1
        best = min(rows, key=lambda r: (json.loads(r["raw_json"])["position"]["accuracy"], r["id"]))
        entries.append((time, best, conflict))
    block["fixes"], block["fix_times"] = entries, [e[0] for e in entries]


def nearest_fix(block, time, max_gap):
    times = block["fix_times"]
    index = bisect_left(times, time)
    candidates = [block["fixes"][i] for i in (index - 1, index) if 0 <= i < len(times)]
    if not candidates:
        return None, "no_valid_mobile_gps"
    candidates.sort(key=lambda f: abs(f[0] - time))
    if abs(candidates[0][0] - time) > max_gap:
        return None, "gps_too_far_from_receive_time"
    if len(candidates) == 2 and math.isclose(abs(candidates[0][0] - time), abs(candidates[1][0] - time), abs_tol=1e-6):
        return None, "ambiguous_gps_tie"
    if candidates[0][2]:
        return None, "conflicting_gps_fixes"
    return candidates[0][1], ""


def tx_evidence(block, row, options):
    left = bisect_right(block["ends"], row["start"] - options["transition_guard"]) - 1
    right = bisect_left(block["starts"], row["end"] + options["transition_guard"])
    if left < 0 or right >= len(block["rows"]):
        return [], "missing_tx_bracket"
    bracket = block["rows"][left:right + 1]
    if bracket[-1]["end"] - bracket[0]["start"] > options["max_gap"]:
        return [], "tx_bracket_too_wide"
    if any(not s["is_in_tx"] or s["is_in_rx"] for s in bracket):
        return [], "opposite_radio_not_consistently_tx"
    return bracket, ""


def align_logs(mobile, fixed, **overrides):
    options = {**DEFAULTS, **overrides}
    if set(options) != set(DEFAULTS):
        raise ValueError("Unknown alignment option")
    for name, value in options.items():
        if not finite(value) or (not name.endswith("offset") and value <= 0):
            raise ValueError(f"{name} must be finite" + (" and positive" if not name.endswith("offset") else ""))
    logs = {"mobile": select_station(mobile, "mobile"), "fixed": select_station(fixed, "fixed")}
    prepared = {role: prepare(log, options[role + "_offset"], options) for role, log in logs.items()}
    indexes = {role: BlockIndex(data[1]) for role, data in prepared.items()}
    gps_rejected = Counter()
    for block in prepared["mobile"][1]:
        gps_track(block, options, gps_rejected)
    sessions = {role: {s["id"]: s for s in log[0]} for role, log in logs.items()}
    results, ignored = [], Counter()
    for role, (samples, _) in prepared.items():
        other = "fixed" if role == "mobile" else "mobile"
        for sample in samples:
            if sample["is_in_tx"] or not sample["is_in_rx"]:
                ignored[role] += 1
                continue
            session = sessions[role][sample["session_id"]]
            try:
                metadata = json.loads(session["metadata_json"])
                label = metadata.get("recording", metadata).get("label", "")
            except (ValueError, TypeError, AttributeError):
                label = ""
            row = dict(receiver=role, direction="downlink" if role == "mobile" else "uplink",
                       receiver_station=session["station"], receiver_session=session["id"], receiver_sample=sample["id"],
                       receiver_label=label, source=session["source"], original_time=sample["received_at"],
                       original_request_time=sample.get("original_request_time"),
                       corrected_time=iso(sample["time"]) if "time" in sample else None,
                       corrected_request_time=iso(sample["start"]) if "start" in sample else None,
                       corrected_reply_time=iso(sample["end"]) if "end" in sample else None,
                       receiver_offset=options[role + "_offset"],
                       transmitter_offset=options[other + "_offset"],
                       status_latency=sample["end"] - sample["start"] if "start" in sample else None,
                       rssi=sample["rssi_value"] if finite(sample["rssi_value"]) else None,
                       rssi_unit=sample["rssi_unit"], squelch_open=bool(sample["squelch_open"]),
                       channel=sample["channel_id"], **sample_frequencies(sample),
                       status="unmatched", reason="", lat=None, lon=None,
                       gps_session=None, gps_sample=None, gps_time=None, gps_gap=None, gps_accuracy=None,
                       transmitter_session=None, transmitter_samples=[], tx_bracket_seconds=None,
                       tx_before_gap=None, tx_after_gap=None)
            results.append(row)
            if sample["timing_error"]:
                row["reason"] = sample["timing_error"]
                continue
            if row["rssi"] is None or not 0 <= row["rssi"] <= 100 or row["rssi_unit"] != "benshi_relative_0_100":
                row["reason"] = "unsupported_rssi"
                continue
            if sample["direction"] != row["direction"]:
                row["reason"] = "inconsistent_receive_direction"
                continue
            opposite, reason = indexes[other].at(sample["start"], sample["end"])
            own, own_reason = indexes[role].at(sample["start"], sample["end"])
            if reason or own_reason:
                row["reason"] = reason or own_reason
                continue
            gps_block = own if role == "mobile" else opposite
            fix, reason = nearest_fix(gps_block, sample["time"], options["max_gps_gap"])
            if reason:
                row["reason"] = reason
                continue
            row.update(lat=fix["latitude"], lon=fix["longitude"], gps_session=fix["session_id"], gps_sample=fix["id"],
                       gps_time=fix["position_time"], gps_gap=abs(seconds(fix["position_time"]) - sample["time"]),
                       gps_accuracy=json.loads(fix["raw_json"])["position"]["accuracy"])
            bracket, reason = tx_evidence(opposite, sample, options)
            row["status"], row["reason"] = ("unconfirmed", reason) if reason else ("associated", "")
            if bracket:
                row.update(transmitter_session=bracket[0]["session_id"], transmitter_samples=[b["id"] for b in bracket],
                           tx_bracket_seconds=bracket[-1]["end"] - bracket[0]["start"],
                           tx_before_gap=sample["start"] - bracket[0]["end"],
                           tx_after_gap=bracket[-1]["start"] - sample["end"])
    results.sort(key=lambda r: (r["corrected_time"] is None, r["corrected_time"] or "", r["receiver"], r["receiver_sample"]))
    return dict(rows=results, options=options, counts=dict(Counter(r["status"] for r in results)),
                reasons=dict(Counter(r["reason"] for r in results if r["reason"])),
                ignored_non_receive=dict(ignored), rejected_gps=dict(gps_rejected),
                sessions={role: list(items.values()) for role, items in sessions.items()})
