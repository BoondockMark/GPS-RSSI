"""Read-only input selection and creation of a separate combined survey bundle."""

import csv
import json
from pathlib import Path

from .alignment import RESULT_FIELDS, align_logs
from .combined_mapping import render_combined_map
from .measurements import utc_now
from .storage import load_log


def combine(mobile, fixed, output, *, mobile_station=None, fixed_station=None,
            mobile_session=None, fixed_session=None, source="live", **options):
    mobile, fixed, output = (Path(p).expanduser().resolve() for p in (mobile, fixed, output))
    if source not in ("live", "replay"):
        raise ValueError("Source must be live or replay")
    if mobile == fixed or mobile.samefile(fixed):
        raise ValueError("Supply separate mobile and fixed database files")
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    logs = [load_log(path, station=station, session=session, source=source)
            for path, station, session in ((mobile, mobile_station, mobile_session), (fixed, fixed_station, fixed_session))]
    result = align_logs(*logs, **options)
    summary = {k: v for k, v in result.items() if k != "rows"}
    summary.update(created_at=utc_now(), inputs={"mobile": str(mobile), "fixed": str(fixed)}, source=source,
                   selections=dict(mobile_station=mobile_station, fixed_station=fixed_station,
                                   mobile_session=mobile_session, fixed_session=fixed_session))
    # Render/serialize before creating output so validation errors leave no bundle.
    page = render_combined_map(result["rows"], summary)
    report = json.dumps(summary, indent=2, allow_nan=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # Exclusive ownership; never replace an existing survey.
    created = []
    try:
        for name, content in (("map.html", page), ("summary.json", report)):
            path = output / name
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                created.append(path)
                stream.write(content)
        path = output / "measurements.csv"
        with path.open("x", encoding="utf-8", newline="") as stream:
            created.append(path)
            writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
            writer.writeheader()
            for row in result["rows"]:
                writer.writerow({**row, "transmitter_samples": json.dumps(row["transmitter_samples"])})
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        try:
            output.rmdir()
        except OSError:
            pass  # Preserve anything another process put in the directory.
        raise
    return summary
