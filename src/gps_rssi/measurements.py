"""Pure timestamp, GPS-quality, and receive-point qualification rules."""

from collections import Counter
from datetime import datetime, timezone
import json
import math


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def recorded_sweep_type(metadata):
    """Preserve missing legacy provenance rather than guessing its sweep type."""
    try:
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        session = metadata.get("survey_session") or {}
        mode = metadata.get("sweep_type") or session.get("sweep_type")
        return mode if mode in ("passive", "active") else "unclassified"
    except (ValueError, TypeError, AttributeError):
        return "unclassified"


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timestamp lacks UTC offset")
    return result


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


FREQUENCY_FIELDS = ("frequency_hz", "rx_frequency_hz", "tx_frequency_hz")


def channel_frequencies(frame):
    """Configured channel frequencies: Benlink MHz converted to integer Hz.

    These settings are read separately from status, not measured RF frequencies.
    Missing/disabled frequencies remain unknown rather than becoming zero.
    """
    channel = frame.get("channel")
    if channel is None:
        return dict.fromkeys(FREQUENCY_FIELDS)
    if not isinstance(channel, dict):
        raise ValueError("channel must be an object")
    status = frame["status"]
    if type(channel.get("channel_id")) is not int or channel["channel_id"] != status["curr_ch_id"]:
        raise ValueError("channel.channel_id must match status.curr_ch_id")
    def hz(name):
        value = channel.get(name)
        if value is None:
            return None
        if not finite(value) or not 0 <= value <= 1073.741823:
            raise ValueError(f"channel.{name} must be finite MHz from 0 to 1073.741823")
        return round(value * 1_000_000) or None
    rx, tx = hz("rx_freq"), hz("tx_freq")
    talk_around = channel.get("talk_around", False)
    if not isinstance(talk_around, bool):
        raise ValueError("channel.talk_around must be a boolean")
    return dict(frequency_hz=(rx if talk_around else tx) if status["is_in_tx"] else rx,
                rx_frequency_hz=rx, tx_frequency_hz=tx)


def sample_frequencies(sample):
    """Normalize optional columns for old recordings and hand-built samples."""
    return {key: sample.get(key) for key in FREQUENCY_FIELDS}


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
                       "channel": sample["channel_id"], **sample_frequencies(sample)})
    return points, dict(excluded)
