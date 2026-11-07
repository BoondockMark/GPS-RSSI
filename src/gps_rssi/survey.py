"""Build portable multi-transceiver surveys from read-only recording snapshots."""

from collections import Counter
from contextlib import closing
import csv
from importlib.resources import files
import json
from pathlib import Path
import re
import shutil
import tempfile

from .gis import FORMATS, export_points
from .measurements import finite, gps_problem, sample_frequencies, select_points, utc_now
from .sharing import merge_databases, readonly
from .storage import export_csv, load_log


def survey_data(path, *, station=None, session=None, source="live", max_age=10, max_accuracy=100):
    if source not in ("live", "replay", "all"):
        raise ValueError("Source must be live, replay, or all")
    if not finite(max_age) or max_age <= 0 or not finite(max_accuracy) or max_accuracy <= 0:
        raise ValueError("GPS limits must be finite and positive")
    sessions, samples, events = load_log(Path(path), station=station, session=session, source=source)
    points, excluded = select_points(sessions, samples, max_age=max_age, max_accuracy=max_accuracy)
    origins = {}
    with closing(readonly(path)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='samples_origins' AND type='table'").fetchone():
            origins = dict(db.execute("SELECT local_id, original_id FROM samples_origins"))
    by_session = {s["id"]: s for s in sessions}
    by_sample = {s["id"]: s for s in samples}
    labels = {}
    for s in sessions:
        try:
            metadata = json.loads(s["metadata_json"])
            labels[s["id"]] = metadata.get("recording", metadata).get("label", "")
        except (ValueError, AttributeError, TypeError):
            labels[s["id"]] = ""
    for p in points:
        sample = by_sample[p["id"]]
        p.update(original_sample=origins.get(p["id"], p["id"]), rssi_unit=sample["rssi_unit"],
                 gps_accuracy=json.loads(sample["raw_json"])["position"]["accuracy"],
                 receiver_label=labels[p["session"]], direction="downlink")
    mapped = {p["id"]: p for p in points}
    observations = []
    for sample in samples:
        if sample["is_in_tx"] or not sample["is_in_rx"]:
            continue
        s = by_session[sample["session_id"]]
        supported = (sample["rssi_unit"] == "benshi_relative_0_100" and
                     finite(sample["rssi_value"]) and 0 <= sample["rssi_value"] <= 100)
        reason = ("unsupported_rssi" if not supported else "fixed_station_no_mobile_location" if s["role"] != "mobile"
                  else "inconsistent_receive_direction" if sample["direction"] != "downlink"
                  else gps_problem(sample, max_age, max_accuracy))
        p = mapped.get(sample["id"], {})
        observations.append(dict(id=sample["id"], original_sample=origins.get(sample["id"], sample["id"]),
                                 station=s["station"], session=s["id"], source=s["source"], role=s["role"],
                                 label=labels[s["id"]], time=sample["received_at"],
                                 direction="downlink" if s["role"] == "mobile" else "uplink",
                                 rssi=sample["rssi_value"] if supported else None, rssi_unit=sample["rssi_unit"],
                                 squelch_open=bool(sample["squelch_open"]), channel=sample["channel_id"],
                                 **sample_frequencies(sample),
                                 lat=p.get("lat"), lon=p.get("lon"), location_reason=reason or ""))
    summary = dict(created_at=utc_now(), source=source, selected_samples=len(samples),
                   receive_observations=len(observations), mapped_points=len(points),
                   station_count=len({s["station"] for s in sessions}), session_count=len(sessions),
                   excluded=excluded, ignored_non_receive=len(samples) - len(observations),
                   location_reasons=dict(Counter(r["location_reason"] for r in observations if r["location_reason"])),
                   gps_limits=dict(max_age=max_age, max_accuracy=max_accuracy), sessions=sessions,
                   event_count=len(events))
    return points, observations, summary


def render_survey(observations, summary):
    assets = files("gps_rssi").joinpath("assets")
    payload = json.dumps(dict(observations=observations, summary=summary), allow_nan=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    replacements = {
        "__LEAFLET_CSS__": assets.joinpath("leaflet/leaflet.css").read_text(encoding="utf-8"),
        "__LEAFLET_JS__": assets.joinpath("leaflet/leaflet.js").read_text(encoding="utf-8"),
        "__VISUAL_CSS__": assets.joinpath("visualization.css").read_text(encoding="utf-8"),
        "__VISUAL_JS__": assets.joinpath("visualization.js").read_text(encoding="utf-8"),
        "__BASEMAP_JS__": assets.joinpath("basemap.js").read_text(encoding="utf-8"),
        "__SURVEY_JS__": assets.joinpath("survey.js").read_text(encoding="utf-8"),
        "__DATA__": payload,
    }
    # Replace tokens in one pass so stored labels cannot become template directives.
    return re.sub("|".join(re.escape(k) for k in replacements), lambda m: replacements[m[0]],
                  assets.joinpath("survey.html").read_text(encoding="utf-8"))


def create_survey(paths, output, *, source="live", max_age=10, max_accuracy=100):
    """Stage a complete bundle, then publish to a newly owned output directory."""
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    paths = [Path(p).expanduser().resolve() for p in paths]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gps-rssi-survey-", dir=output.parent) as work:
        stage = Path(work)
        merge_counts = merge_databases(paths, stage / "recordings.sqlite")
        points, observations, summary = survey_data(stage / "recordings.sqlite", source=source,
                                                   max_age=max_age, max_accuracy=max_accuracy)
        summary.update(inputs=[str(p) for p in paths], merge=merge_counts,
                       recordings_scope="All input sessions, including live/replay, TX, idle, and diagnostic events")
        # CSV and map use the selected source. The lossless DB retains all data.
        export_csv(stage / "recordings.sqlite", stage / "measurements.csv", source=source)
        for format in FORMATS:
            export_points(points, stage / ("measurements." + format), format, metadata=summary)
        (stage / "map.html").write_text(render_survey(observations, summary), encoding="utf-8")
        (stage / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
        license = assets_license()
        (stage / "Leaflet-LICENSE.txt").write_text(license, encoding="utf-8")
        output.mkdir()  # Never overwrite a destination that appeared during staging.
        created = []
        try:
            for path in stage.iterdir():
                destination = output / path.name
                with path.open("rb") as src, destination.open("xb") as dst:
                    created.append(destination)
                    shutil.copyfileobj(src, dst)
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            try:
                output.rmdir()
            except OSError:
                pass
            raise
    return summary


def assets_license():
    return files("gps_rssi").joinpath("assets/leaflet/LICENSE").read_text(encoding="utf-8")
