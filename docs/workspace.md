# Operator workspace

The local workspace brings radio setup, recording controls, live views, survey
exports, and audio tests into one browser interface. The existing commands do
the work in managed background processes; ordinary recording still requests no
RF transmissions or radio-setting changes.

## Open the app

Run `install.cmd` on Windows or `./install.sh` on Linux. Then double-click
`start-app.cmd` on Windows or run `./start-app.sh` on Linux. A browser opens at `http://127.0.0.1:8780`.
The console stays open while the app runs. Installation is required once; the
launcher uses the environment prepared by the installer. After updating the
checkout, close the workspace and rerun the installer before launching again.

The workspace manages operations it launches. It does not adopt an existing CLI
recorder or another control application's connection. Stop those before starting
the same radio here; their saved setup and recordings can still be recognized.

The equivalent command is:

```sh
gps-rssi workspace
```

`--directory PATH` chooses the app folder, `--port N` changes the workspace port,
and `--no-browser` starts without opening a tab. The workspace is local to this
computer. It is not a LAN dashboard or live synchronization server.

Closing a tab does **not** stop operations. Use **Exit workspace** to request
cleanup of managed recorders/tests and save data. Ctrl-C in the app console also
requests cleanup. A second workspace for the same folder is refused.

## Radios

Select **Find nearby radios**, then **Use this radio**, or select **Add radio**
to enter an address manually. Bluetooth discovery does not pair devices or
verify an advertised model. Complete host/radio Bluetooth pairing before use;
audio also requires the radio's Classic Bluetooth audio service.
Discovered addresses that already belong to a saved radio open **Edit radio**
with its existing settings, rather than creating a duplicate setup.

Give each physical transceiver a distinct, recognizable name. Choose **Mobile**
for a moving station with GPS or **Fixed** for a site monitor. Add model, antenna,
mounting, and channel notes so comparisons retain the correct context. The app
chooses separate recording files and live-view ports. Advanced setup can change
those locations/ports inside the app folder.

New personal files use `NAME.radio.toml` in the app folder and are Git-ignored.
Existing `mobile.toml`, `fixed.toml`, and other root-level radio TOML setups are
recognized. Existing comments and unrelated settings are preserved on edits;
prior files are backed up in `setup-backups/`. Stale edits, conflicting names,
addresses, files, or ports are refused. Stop a radio's active operation before
editing it. Invalid setup files show an error and need correction before use.

**Start recording** launches that radio's recorder and dashboard. You can start
multiple cards independently. **Stop recording** requests graceful shutdown;
saved samples remain. A card distinguishes a running recorder from its latest
telemetry time: a process running is not proof that the radio is connected.
Receive RSSI and GPS lock are shown separately. GPS lock does not establish a
qualified map point or known accuracy. RSSI is relative, not calibrated dBm.
**Disabled** means GPS recording is turned off in that radio's setup; it does
not indicate a radio power state. **Unknown** means no GPS telemetry has been
recorded yet. **Last recorded GPS lock** labels saved history when the recorder
is stopped or telemetry is older than 30 seconds. Only a running recorder with
recent telemetry shows the **GPS lock** label.

**Snapshot** makes a standalone portable SQLite file, including committed WAL
data while recording continues. Download it from Activity to exchange with
another operator; do not copy only the main file of a running SQLite database.

## Live view

Choose a running recorder or select **Live view** from its radio card. The
existing live map/dashboard opens within the workspace, with its normal map,
chart, pause, follow, reset, and exit controls. **Open in new window** and
**Reload view** are available. A new recorder may need a moment to open its local
dashboard. Stopped recordings are explored through Surveys & exports.

Exiting a per-radio dashboard stops only that recorder. Exiting the whole
workspace stops its managed operations. Viewed street tiles are cached on this
computer and reused across surveys and app restarts while fresh. Missing or
expired tiles require Internet access; local measurements and charts still work
without a street background. Open surveys using **Activity → Open survey** for
the persistent cache. Downloaded HTML opened as a file shows measurements without
requesting street tiles. See the README for cache location and expiry behavior.

## Surveys & exports

Select existing recordings or use **Import recording** to upload a portable
SQLite file from another operator. Imports are copied into app-managed storage;
original files are unchanged. Uploads are limited to 128 MB each. The app checks
the recording schema/integrity before adding a file to the library.

**Create survey** makes a multi-transceiver map, comparison charts, all four GIS
formats, CSV, summary JSON, and a lossless merged recording database. Repeated
inputs and overlapping snapshots are deduplicated using original identities.
Choose recorded data, replay only, or both with explicit labeling. Fixed receive
history remains separate without an invented mobile location. **Merge recordings
only** creates just the combined database.

For one file, select a recording and export GeoPackage, GeoJSON, KML, GPX, or CSV.
GIS contains qualified mobile receive points; CSV retains selected sample states.
**Create portable snapshot** includes all committed sessions and diagnostics in
the selected recording. Source/view filters do not trim a portable snapshot.

Finished results appear in **Activity**. Open a survey/report, download individual
files, or download a ZIP containing the result folder. Bundles contain precise
locations and setup metadata, and a survey's merged database retains all input
sources, including ones excluded from the map. Sharing is through downloaded
files, not public links or automatic uploads.

New surveys include three linked ways to inspect performance:

1. **Route and signal versus distance** colors observed route segments and links
   distance-chart selections to the original map measurement. Long pauses, GPS
   gaps, channel changes, and implausible jumps break route continuity; distances
   exclude those gaps.
2. **Compare two recording sessions** opens maps with synchronized position/zoom
   and charts on common scales. Select each recording independently. Global survey
   filters apply to both, and an empty pane explains when a recording is filtered
   out. Distance starts independently for each run. **Compare nearby measurements**
   pairs each A reading with its nearest unused B reading in A time order, within
   the chosen radius (25 m default; 1–200 m). Same receive direction and, by default,
   the same known recorded frequency are required. The checkbox can explicitly
   relax frequency matching. Inspecting a pair highlights both original readings;
   B − A remains receiver-relative, not a calibrated gain or dB. GPS error and
   parallel roads can make proximity misleading. Matching uses at most 5,000
   eligible readings per session and reports any truncation; up to 200 pairs are
   listed, with counts for all computed matches.
3. **Terrain and radio path** lets you load a route's USGS ground-elevation profile
   alongside signal. To inspect a path, select a located observation, enter the
   assumed fixed transmitter coordinates and both antenna heights above ground,
   then choose **Inspect selected path (USGS)**. The cross-section shows sampled
   ground, the estimated antenna line and a 60% first-Fresnel-radius envelope,
   including Earth curvature. The recorded receive frequency is used by default;
   an explicit frequency in MHz can be entered. Missing frequency keeps Fresnel
   clearance unknown while the line-of-sight check still runs. Red rings mark
   line obstruction; amber marks Fresnel intrusion when the line is clear; green
   means both checks pass at sampled locations; gray indicates unknown results.

Elevation lookups are explicit: the buttons send sampled coordinates to USGS EPQS
(primarily US coverage), with results cached locally for 30 days. Missing elevations
produce an unknown result. Route profiles use at most 96 measurements; path checks
use 65 ground samples and show their spacing. The Fresnel envelope uses a
near-horizontal straight-path approximation. Buildings, trees and atmospheric
refraction are omitted, and unsampled ridges can be missed.
A sampled-clear result does not establish radio coverage. Station settings and
path results last for this page only; changing station, heights or frequency clears previous path
results. Cancel stops applying a pending result, though an in-flight query may
finish populating the cache. Generate a new survey after updating to get these tools.

The advanced two-station time-association workflow remains available through
`combine`; the shared-survey UI does not infer which of several simultaneous
mobiles produced a fixed receive observation.

## Audio tests

The default installer includes audio support. If you previously used `--basic`,
close the workspace and rerun `install.cmd` on Windows or `./install.sh` on Linux
without that option, then restart the workspace.

The page guides three steps:

1. **Capture receiver:** choose a radio, set capture duration, and start receive-only
   capture. Wait for **Capturing** in Activity. Stop ordinary recording on that
   radio first. **Check audio support** inspects its advertised audio services.
   **Cancel capture** keeps audio for diagnosis, but a timed capture must finish
   normally before it is offered for analysis.
2. **Prepare or transmit:** create a local preview to listen without any radio
   connection. To transmit, choose a radio and select **Review RF transmission**.
   The review shows the exact radio, address, frequency, duration, and level.
   Confirm that you checked the radio's channel/power and prepared the receiver,
   then select **Transmit test tone**. Review expires after 60 seconds, is single
   use, and is invalidated if its saved setup changes. Merely opening/canceling
   the dialog never starts a transmission.
3. **Analyze:** choose a completed capture or import a 16-bit mono WAV. The report
   includes a SINAD chart, per-window validity, CSV, and full analysis metadata.
   Imported WAV files have no capture timing sidecar. The selected tone frequency
   is used as the expected analysis frequency.

Tone tests retain the CLI's 10-second cap, paced writes, end-of-audio cleanup,
and BLE stop verification. A visible transmit banner provides **Stop tone** from
any workspace page. Keep the radio's own transmit timeout enabled for failures
that software cannot control. SINAD describes the complete audio path, including
codecs/transmitter distortion; it is not calibrated receiver sensitivity.
See [audio/SINAD details and bench acceptance](sinad.md).

## Activity, stop, and recovery

Operations continue across tab changes. Unsaved radio edits and recording
selections are retained during polling. Activity shows current state, elapsed
time, saved artifacts, and optional diagnostic details. A lost workspace
connection does not imply that recording or transmission stopped.

Stop requests signal the child process for graceful cleanup. After a 15-second
grace period, an unresponsive child is terminated, with a bounded forced-cleanup
fallback. A transmit test with unverified cleanup stays marked **Check radio**
and holds that radio's resource reservation. After confirming the operation has
ended and the physical radio is not transmitting, use **Confirm stopped**.
The app will refuse acknowledgement while a process it owns is still running.

If the workspace ended unexpectedly, prior active operations are restored as
unknown. They are not automatically restarted. The app cannot reconnect to an
orphaned process or guarantee RF stop after a crash; inspect the prior process
and radio before acknowledging. Operation logs/results remain in
`data/workspace/jobs/`, and imported files in `data/workspace/imports/`.

## Validation

Python tests cover setup preservation and conflicts, review-token semantics,
resource reservations, graceful stop, restart recovery, imports, request
authentication, and confined result downloads. The browser workflow exercises
real setup/editing, multi-mobile survey ZIPs, GIS export, a no-RF tone preview,
canceled transmission review, audio import/SINAD, responsive layouts, and exit.

```sh
python -m unittest discover -s tests -v
node --test tests/*.test.cjs
python -m pip install playwright
python -m playwright install chromium
python tests/workspace_browser.py
```

Browser checks are also configured in Linux CI. Physical Bluetooth/RF and native
Windows acceptance still require the actual radios. The browser test uses only
synthetic recordings and never starts an RF transmit job.
