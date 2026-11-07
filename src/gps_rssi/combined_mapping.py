"""Standalone two-direction map and table for time-associated observations."""

from html import escape
from importlib.resources import files
import json

from . import review
from .alignment import RESULT_FIELDS


def render_combined_map(rows, summary):
    payload = json.dumps({"rows": rows, "summary": summary, "fields": RESULT_FIELDS}, allow_nan=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    # A static table remains available even without JavaScript or map services.
    table = []
    fields = ("direction", "receiver_label", "receiver_session", "receiver_sample", "corrected_time",
              "rssi", "rssi_unit", "frequency_hz", "lat", "lon", "gps_gap", "status", "reason")
    for index, row in enumerate(rows):
        table.append(f'<tr data-row="{index}"><td></td>' + "".join(f"<td>{escape(str(row.get(k) if row.get(k) is not None else ''))}</td>" for k in fields) + "</tr>")
    before, _, after = TEMPLATE.partition("__TABLE__")
    assets = files("gps_rssi").joinpath("assets")
    before = before.replace("__REVIEW_CONTROLS__", review.CONTROLS)
    after = after.replace("__REVIEW_JS__", review.script())
    before = before.replace("__VISUAL_CSS__", assets.joinpath("visualization.css").read_text(encoding="utf-8"))
    after = after.replace("__VISUAL_JS__", assets.joinpath("visualization.js").read_text(encoding="utf-8"))
    after = after.replace("__SURVEY_JS__", assets.joinpath("combined-survey.js").read_text(encoding="utf-8"))
    return before + "".join(table) + after.replace("__DATA__", payload)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Combined RF survey</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="anonymous">
<style>body{font:16px system-ui;margin:0;color:#152431;background:#f5f7fa}header,main{padding:1rem}
#map{height:65vh;background:#e3e9ed}.scroll{overflow:auto}table{border-collapse:collapse;font-size:.85rem}
td,th{padding:.4rem;border-bottom:1px solid #ddd;text-align:left}#error{color:#9b1c1c}</style><style>__VISUAL_CSS__</style></head>
<body><header><h1>Combined RF survey</h1><p id="summary"></p>
<p>Downlink: fixed to mobile. Uplink: mobile to fixed. Each point uses the mobile GPS position.
RSSI remains relative to its receiver, not dBm. Time association does not identify a transmitter
or establish two-way voice coverage. Unconfirmed results are hidden initially; use Match quality to inspect them.</p>
<p id="error">Map requires JavaScript, Internet access, Leaflet and OpenStreetMap tiles. The table remains available below.</p>
<div class="survey-metrics" aria-label="Current view counts">
<p><strong id="visible-count">—</strong>Receive observations</p><p><strong id="mapped-count">—</strong>Mapped locations</p>
<p><strong id="unconfirmed-count">—</strong>Unconfirmed shown</p><p><strong id="unmatched-count">—</strong>Unmatched shown</p></div>
</header><nav class="view-toolbar" aria-label="Survey filters">
<label>Direction <select id="direction-filter"><option value="both">Both directions</option>
<option value="downlink">Fixed → mobile</option><option value="uplink">Mobile → fixed</option></select></label>
<label>Match quality <select id="quality-filter"><option value="associated">Associated only</option>
<option value="located">Associated + unconfirmed</option><option value="all">All receive observations</option></select></label>
<button id="fit-measurements" type="button">Fit visible measurements</button></nav>
__REVIEW_CONTROLS__
<div id="map" aria-label="Combined directional measurements"></div><main>
<p><span class="signal-scale"></span> Relative RSSI: 0 weaker → 100 stronger. Associated points have dark outlines; unconfirmed points have purple outlines.
Closed-squelch receive points have dashed outlines. The original CSV includes every receive observation and its rejection reason. Use Export cleaned CSV for reviewed results.</p>
<section aria-labelledby="signal-heading"><h2 id="signal-heading">Receive signal history</h2>
<p>Statistics and charts follow the filters. Each receiver session is separate; values are not averaged across radios.
Missing measurements are not zero signal. Timeline samples link to their map location when one is available.</p>
<p id="measurement-selection" class="selection-note" aria-live="polite">Select a timeline point or map marker to inspect an observation.</p>
<div id="signal-charts" class="signal-panels"></div></section>
<details><summary>Receive observations</summary><div class="scroll"><table>
<thead><tr><th>Select</th><th>Direction</th><th>Receiver label</th><th>Session</th><th>Sample</th><th>Corrected UTC time</th>
<th>RSSI</th><th>Unit</th><th>Frequency (Hz)</th><th>Latitude</th><th>Longitude</th><th>GPS gap (s)</th><th>Status</th><th>Reason</th></tr></thead>
<tbody>__TABLE__</tbody></table></div></details></main>
<script id="combined-data" type="application/json">__DATA__</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin="anonymous"></script>
<script>__VISUAL_JS__</script>
<script>__REVIEW_JS__</script>
<script>__SURVEY_JS__</script></body></html>
"""
