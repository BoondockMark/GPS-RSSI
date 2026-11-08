"""Build a portable overview and survey using only one named run's recordings."""

import html
import json
from pathlib import Path
import tempfile

from .measurements import utc_now
from .sharing import snapshot_sessions
from .survey import create_survey, render_survey, survey_data
from .survey_sessions import session_path


def build_results(directory, key, output):
    root, output = Path(directory).resolve(), Path(output).resolve()
    manifest = json.loads(session_path(root, key).read_text(encoding="utf-8"))
    if manifest["id"] != key or manifest["state"] != "finished":
        raise ValueError("Finish the survey and verify its radios are stopped before building results")
    if output.exists():
        raise FileExistsError("Survey results already exist")
    radios = {radio["id"]: radio for radio in manifest["radios"]}
    selected = {}
    for operation in manifest.get("operations", []):
        if operation["state"] in ("running", "stopping", "unknown"):
            raise ValueError("A survey operation has not finished cleanup")
        result = operation.get("result") or {}
        recording_id = result.get("recording_session_id")
        if not recording_id:
            if result.get("samples_recorded", 0):
                raise ValueError("A recording identity is missing; refusing to include unrelated data")
            continue
        radio = radios[operation["profile"]]
        database = (root / radio["database"]).resolve()
        if not database.is_relative_to(root):
            raise ValueError("Survey recordings must stay inside the app folder")
        selected.setdefault(database, set()).add(recording_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".survey-session-", dir=output.parent) as work:
        paths = [snapshot_sessions(database, Path(work) / f"{index}.sqlite", ids)
                 for index, (database, ids) in enumerate(selected.items())]
        if paths:
            summary = create_survey(paths, output)
            summary.update(inputs=[str(p.relative_to(root)) for p in selected],
                recordings_scope="Only recording identities belonging to this named survey session")
        else:
            output.mkdir()
            summary = dict(created_at=utc_now(), selected_samples=0, mapped_points=0,
                receive_observations=0, station_count=0, inputs=[], recordings_scope="No recordings collected")
    summary["survey_session"] = {key: manifest[key] for key in ("id", "name", "notes", "created_at", "finished_at", "radios")}
    summary["tones_sent"] = sum((op.get("result") or {}).get("tones_sent", 0) for op in manifest.get("operations", []))
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    (output / "survey-session.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    if paths:
        _, observations, _ = survey_data(output / "recordings.sqlite")
        (output / "map.html").write_text(render_survey(observations, summary), encoding="utf-8")
    esc = lambda value: html.escape(str(value))
    rows, operations = [], []
    for radio in manifest["radios"]:
        rows.append(f'<li><strong>{esc(radio["name"])}</strong> · {esc(radio["role"])}<p>{esc(radio["label"])}</p></li>')
    for operation in manifest.get("operations", []):
        result = operation.get("result") or {}
        # Job IDs are generated internally; use an index for portable file names.
        log_name = f"operation-{len(operations)+1}.txt"
        (output / log_name).write_text(operation.get("log", ""), encoding="utf-8")
        stop = ("TX verified off" if result.get("stop_verified") is True else
                "TX stop not verified" if result.get("stop_attempted") else "No transmission requested")
        operations.append(f'<li><strong>{esc(operation["title"])}</strong> · {esc(operation["state"])}'
            f'<p>{esc(operation.get("message", ""))}</p><p>{esc(result.get("clock_source", "Clock not recorded"))} · '
            f'{esc(result.get("samples_recorded", 0))} saved samples · {esc(stop)}</p>'
            f'<a href="{log_name}">Diagnostic log</a></li>')
    links = '<a class="button" href="map.html">Open survey map</a>' if paths else '<p>No recording data was collected in this session.</p>'
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(manifest["name"])} · Survey results</title><style>body{{font:16px/1.6 system-ui,sans-serif;color:#17384b;background:#f3f7f9;margin:0;padding:24px}}main{{max-width:900px;margin:auto;background:white;padding:28px;border-radius:12px}}h1{{overflow-wrap:anywhere}}p,li{{overflow-wrap:anywhere}}.notes{{white-space:pre-wrap}}li{{margin:18px 0}}li p{{margin:5px 0}}a{{color:#086e74}}.button{{display:inline-block;background:#087b7d;color:white;padding:12px 20px;border-radius:6px;text-decoration:none}}dl{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}dd{{margin:0;font-size:24px;font-weight:700}}</style></head>
<body><main><p>FIELD SURVEY RESULTS</p><h1>{esc(manifest["name"])}</h1><p>{esc(manifest["created_at"])} → {esc(manifest["finished_at"])}</p>
<p class="notes">{esc(manifest["notes"])}</p><dl><div><dt>Saved samples</dt><dd>{summary["selected_samples"]}</dd></div><div><dt>Completed tones</dt><dd>{summary["tones_sent"]}</dd></div></dl>
{links}<h2>Radios and antenna notes</h2><ul>{''.join(rows)}</ul><h2>Operations and diagnostics</h2><ul>{''.join(operations) or '<li>No radio operations were started.</li>'}</ul>
<p>Results include only recordings collected for this survey. Earlier and later runs are excluded; the original radio databases are retained.</p><a href="survey-session.json">Session details</a> · <a href="summary.json">Summary</a></main></body></html>'''
    (output / "index.html").write_text(page, encoding="utf-8")
    return summary
