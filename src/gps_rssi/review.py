"""Shared controls for non-destructive review of standalone survey maps."""
from importlib.resources import files

CONTROLS = """<style>
.review-controls{padding:1rem;background:#fff;border-block:1px solid #cbd5e1}
.review-controls h2{font-size:1.2rem;margin:0 0 .6rem}
.review-controls button,.review-controls label{font:inherit;min-height:40px;margin:.2rem;padding:.45rem;display:inline-flex;align-items:center;gap:.4rem}
.review-controls button:not(:disabled){cursor:pointer}
.review-controls button:focus-visible,.review-controls input:focus-visible{outline:3px solid #4f46e5;outline-offset:2px}
#review-status{font-weight:600}
</style><section class="review-controls" aria-label="Review observations">
<h2>Review before export</h2>
<p>Enable map selection and click points, or use the table checkboxes. Remove selected observations to exclude them from the map, statistics and cleaned CSV. Export includes all retained observations, including those hidden by filters.</p>
<label><input id="review-mode" type="checkbox"> Select points on map</label>
<button id="review-select" type="button">Select all visible</button>
<button id="review-clear" type="button">Clear selection</button>
<button id="review-remove" type="button" disabled>Remove selected</button>
<button id="review-undo" type="button" disabled>Undo removal</button>
<button id="review-export" type="button">Export cleaned CSV</button>
<p id="review-status" role="status" aria-live="polite"></p>
<p>Edits stay in this browser tab; download the cleaned CSV before closing. Original recordings and previously exported files are unchanged.</p>
</section>"""


def script():
    return files("gps_rssi").joinpath("assets/survey-review.js").read_text(encoding="utf-8")
