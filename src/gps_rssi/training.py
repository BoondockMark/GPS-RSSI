"""Identity and instructions for explicitly synthetic training recordings."""

import json

KIND = "gps-rssi-training-v1"
STATION = dict(lat=33.96, lon=-117.95)


def descriptor(sessions):
    if not sessions:
        return None
    for session in sessions:
        try:
            metadata = json.loads(session["metadata_json"])
            if session["source"] != "replay" or metadata.get("training") != KIND:
                return None
        except (ValueError, TypeError, AttributeError, KeyError):
            return None
    return dict(kind=KIND, station=STATION.copy(), warning="SYNTHETIC TRAINING — not measured coverage or real ground elevation")


GUIDE = """GPS-RSSI OFFLINE TEST SURVEY

These recordings and elevations are entirely synthetic. Locations illustrate
controls only; they do not describe actual radio coverage or ground elevation.
No radio, RF transmission, USGS request, or elevation-cache change is needed.

1. Open map.html. It works as a standalone file without Internet access.
   Or use Activity > Open survey after Create test survey in the workspace.
2. Use the Synthetic training panel to try each preset:
   - Sampled clear: flat synthetic ground, 60 m antenna heights; green ring.
   - Fresnel intrusion: flat ground, 5 m heights; line clear, amber ring.
   - Blocked line: synthetic ridge, 60 m heights; red ring.
   - Missing elevation: unknown ground; gray ring, no clearance conclusion.
   - Missing frequency: flat ground, unknown recorded frequency; gray ring.
3. Change antenna heights or choose Enter frequency (MHz), then inspect again.
   Changing settings clears previous results. Presets use the displayed dummy site
   at 33.96, -117.95. These are example coordinates, not your fixed station.
   Replay signal readings stay fixed: this is not a propagation simulator.
   Try changing Maximum desired spacing and Query budget. Paths refine adaptively;
   the status reports actual spacing and warns when the budget limits sampling.
   Elevation source details identify analytic fixtures: native cell size and
   acquisition date are not applicable. Real surveys show available USGS metadata.
4. Load synthetic route elevations and select points in the aligned plots.
5. Show side-by-side comparison. Run B travels in reverse and has deliberately
   stronger readings (+8 relative points, not measured antenna gain). The default
   25 m radius gives 29 pairs. At 5 m there are none. Unchecking Same recorded
   frequency only gives 31 pairs, including unknown/different-frequency samples.
6. Explore filters and GPS exclusions. Run A has a missing fix and a long pause.
   Route lines break at those gaps rather than inventing continuous travel.

All samples retain source=replay. recordings.sqlite can be imported into the app
and used to create another survey; choose Synthetic replay only. Training mode
is preserved only when all selected sessions are marked training recordings.
Mixing real data disables the synthetic terrain adapter. Exported CSV/GIS values
are synthetic too. Keep them separate from operational survey datasets.

Background tiles start off. In the app, selecting a real basemap needs Internet
unless its viewed tiles are cached. Background tile availability does not affect
these local terrain cases. No elevation results are sent to USGS or saved in its
cache. Antenna settings, scenario choices and inspected paths last for this page.
"""
