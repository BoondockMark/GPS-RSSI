"""Read-only comparison bundles built from two completed scoped sweep snapshots."""

from contextlib import closing
import json
from pathlib import Path

from .result_metrics import result_quality, with_gps_coverage
from .results_library import completed_source
from .sharing import readonly
from .survey import create_survey, render_survey, survey_data
from .survey_sessions import sweep_mode


def compare_results(directory, first, first_result, second, second_result, output):
    if first == second:
        raise ValueError("Choose two different sweeps")
    root, output = Path(directory).resolve(), Path(output).resolve()
    runs, paths = [], []
    for key, job_id in ((first, first_result), (second, second_result)):
        manifest, database = completed_source(root, key, job_id)
        with closing(readonly(database)) as db:
            recordings = [row[0] for row in db.execute("SELECT id FROM sessions ORDER BY started_at,id")]
        runs.append(dict(id=key, result_job=job_id, name=manifest["name"], sweep_type=sweep_mode(manifest.get("sweep_type")),
                         recording_ids=recordings, radios=manifest["radios"]))
        paths.append(database)
    if set(runs[0]["recording_ids"]) & set(runs[1]["recording_ids"]):
        raise ValueError("These sweeps share recording identities; choose independent runs")
    summary = create_survey(paths, output)
    _, observations, _ = survey_data(output / "recordings.sqlite")
    defaults = []
    for run in runs:
        defaults.append(next((row["session"] for row in observations if row["session"] in run["recording_ids"]
                             and row["lat"] is not None and row["lon"] is not None), None))
    if any(value is None for value in defaults):
        raise ValueError("Both sweeps need mapped measurements for location comparison")
    summary.update(comparison=dict(runs=runs, default_a=defaults[0], default_b=defaults[1],
        mixed_sweep_types=runs[0]["sweep_type"] != runs[1]["sweep_type"]),
        inputs=[str(path.relative_to(root)) for path in paths],
        recordings_scope="Only the scoped snapshots of the two selected completed sweeps")
    summary["result_quality"] = with_gps_coverage(result_quality(output / "recordings.sqlite"), summary["mapped_points"])
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    (output / "map.html").write_text(render_survey(observations, summary), encoding="utf-8")
    (output / "comparison.json").write_text(json.dumps(summary["comparison"], indent=2, allow_nan=False), encoding="utf-8")
    return summary
