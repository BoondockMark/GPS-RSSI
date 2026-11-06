"""Pure timestamp, GPS-quality, and receive-point qualification rules."""

from collections import Counter
from datetime import datetime, timezone
import json
import math


def utc_now():
    return datetime.now(timezone.utc).isoformat()


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


def select_points(sessions, samples, *, max_age=10, max_accuracy=100):
    sessions_by_id = {s["id"]: s for s in sessions}
    points, excluded = [], Counter()
    for sample in samples:
        session = sessions_by_id[sample["session_id"]]
        reason = None
        if session["role"] != "mobile":
            reason = "not_mobile"
        elif sample["is_in_tx"] or not sample["is_in_rx"] or sample["direction"] != "downlink":
            reason = "not_downlink_receive"
        elif sample["rssi_unit"] != "benshi_relative_0_100" or not finite(sample["rssi_value"]) or not 0 <= sample["rssi_value"] <= 100:
            reason = "unsupported_rssi"
        else:
            reason = gps_problem(sample, max_age, max_accuracy)
        if reason:
            excluded[reason] += 1
            continue
        points.append({"id": sample["id"], "station": session["station"],
                       "session": sample["session_id"], "source": session["source"],
                       "time": sample["received_at"], "position_time": sample["position_time"],
                       "lat": sample["latitude"], "lon": sample["longitude"],
                       "rssi": sample["rssi_value"], "squelch_open": bool(sample["squelch_open"]),
                       "channel": sample["channel_id"]})
    return points, dict(excluded)
