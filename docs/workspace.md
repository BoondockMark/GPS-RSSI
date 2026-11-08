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

To explore without collecting a survey, select **Create test survey**, then
**Activity → Open survey**. No saved radio, Bluetooth connection, or existing
recording is needed. The synthetic training panel has five preset buttons:

| Preset | Expected result |
| --- | --- |
| Sampled clear | Flat fictitious ground, 60 m antenna heights; green ring |
| Fresnel intrusion | Flat fictitious ground, 5 m heights; clear line, amber ring |
| Blocked line | Fictitious ridge, 60 m heights; red ring |
| Missing elevation | Unknown terrain clearance; gray ring |
| Missing frequency | Line check runs, Fresnel result remains unknown; gray ring |

Presets enter a dummy station, select a reading, and run the check locally. Inputs
remain editable. Load synthetic route elevations, inspect nearby measurement
pairs, change filters, or try manual frequency entry. Replay RSSI stays fixed;
this is not a propagation simulator. No USGS requests or service-cache writes
occur, and backgrounds start off. The ZIP contains **TEST-SURVEY.txt** with a
walkthrough, plus **map.html**, which works without the app or Internet access.
Real background tiles remain optional and may require Internet access.

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
   means both checks pass at sampled locations; gray indicates unknown or
   sampling-limited results.

Elevation lookups are explicit: the buttons send sampled coordinates to USGS EPQS
(primarily US coverage), with results cached locally for 30 days. Missing elevations
produce an unknown result. Route profiles use at most 96 measurements; path checks
start with up to 17 pilot locations and refine toward the smaller of the desired
spacing (default 10 m) and verified source cell dimensions. Set a query budget of
257, 1,025 (default), or 4,097 locations. Targets have a 0.5 m floor. Actual spacing
and any budget/floor limit are reported; long paths can take minutes. Unknown
source resolution uses the desired spacing and remains labeled unknown.

Expand **Elevation source details** below either plot for raster IDs, raw
resolution and units, verified cell dimensions, acquisition dates when supplied,
and UTC retrieval times. EPQS draws on USGS 3DEP data at varying resolutions.
Metre elevation output does not establish horizontal resolution: source raster
information is checked when EPQS omits horizontal units. Angular cell dimensions
are approximate metres at each latitude. Cell size is not vertical accuracy.
Legacy cached elevations have unknown provenance; check **Refresh cached elevations**
and reload to obtain available metadata. Metadata lookup failure retains the
elevation with unknown resolution. The offline demo explicitly identifies its
analytic fixtures and has no geographic grid resolution or acquisition date.

The Fresnel envelope uses a
near-horizontal straight-path approximation. Buildings, trees and atmospheric
refraction are omitted, and unsampled ridges or finer source data between
queried locations can be missed.
A sampled-clear result does not establish radio coverage. Station settings and
path results last for this page only; changing station, heights or frequency clears previous path
results. Cancel stops applying a pending result, though an in-flight query may
finish populating the cache. Generate a new survey after updating to get these tools.

The advanced two-station time-association workflow remains available through
`combine`; the shared-survey UI does not infer which of several simultaneous
mobiles produced a fixed receive observation.

## Offline data

Open **Offline data** to import a regional raster MBTiles background, save/import
portable map-and-elevation packs, or enable **Offline mode**. In that mode,
map/elevation requests use saved data only, even if expired; missing locations
remain unknown and enter a persistent queue. **Fill missing data** processes that
queue after reconnecting and turning Offline mode off. Reopen maps after a mode
change, and repeat path inspection after filling.

Saving a pack pins current cache entries against eviction. Transfer recordings
separately using portable snapshots. Raster map coverage and exact queried USGS
elevation locations have different limits; see the
[offline preparation and transfer guide](offline-data.md) before relying on a pack
for a drive without cellular service.

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

### Automatic tone mode

In Audio, select a saved radio and enable **Automatic mode** before reviewing RF
transmission. It sends 1 kHz for five seconds every twenty seconds until halted.
The saved role sets the first start: next minute for fixed, next minute plus ten
seconds for mobile. Activity shows the selected GPS/computer clock and next tone.
Use **Halt automatic mode** in the persistent banner or Activity to stop, including
while armed. The fifteen seconds off allow the radio to receive; this mode does
not record received audio or RSSI. See [timing and CLI details](sinad.md#automatic-fixedmobile-cycle).

## Field survey

Use **Field sweeps** for automatic tone tests with RSSI/GPS recording on the
same radio. Select the saved radio, then choose **Check readiness**. Preflight
opens the control and audio connections without transmitting, checks the encoder
and recording destination, and identifies GPS or computer timing. Each result
shows Ready, Needs attention, or Unavailable with a corrective step. A successful
check is valid for two minutes and is invalidated by setup changes. Computer-clock
fallback can proceed after reviewing it and synchronizing the computer clock.

Choose **Review & start survey**, verify the physical radio channel and power,
and confirm to arm. The screen shows fixed/mobile offsets, a seconds countdown,
clock source separately from position GPS, recent receive RSSI, saved sample count,
and completed tones. Countdown uses the host equivalent of the scheduler deadline,
so a GPS/host clock offset does not make it incorrect. No RF starts from preflight.

**Focus view** hides setup and Activity. **Night display** increases contrast for
low-light use; the preference and selected radio are remembered locally. Neither
preference starts an operation. The large **Halt automatic mode** control requests
cleanup; Stopping remains distinct from TX verified off. The persistent transmit
banner remains available across views. Workspace disconnection or stale session
reports show unknown radio state rather than a live countdown or receive reading.

Samples are saved between tones in the radio's existing recording file and can
be combined/exported normally after stopping. There is no separate live-map
server during a field session; the field monitor shows its telemetry. Received
audio is not recorded. Closing the browser keeps the session running; halt or
Exit workspace requests cleanup. Physical radio tests are still required.

## Named survey sessions

In **Field sweeps**, choose **New session**, enter a name and site/antenna/route
notes, and select one or more saved radios. Creation saves the plan without
opening a radio connection, recording, or transmitting. Radio setup and antenna
notes are captured for the run. If a setup changes, create a new session.

Select each participating radio, run its preflight, and review/start it normally.
Each radio is armed individually; selecting several radios does not start them.
Preflight checks and starts are associated with the selected session. The same
radio may collect several recordings in one open session, for example after a
verified halt. The session and selected radio are remembered across page reloads;
reload never starts an operation. **Ungrouped run** retains the previous workflow.

**Halt automatic mode** stops the selected radio's cycle. **Finish survey** stops
all operations owned by that named session, including a pending preflight, and
waits for cleanup. It does not stop other sessions or unrelated recorders. New
starts and previously reviewed starts are refused once finishing begins.
Unknown/unverified operation stops leave the session needing attention: check
the radios, acknowledge the affected operation in Activity, then retry finishing.
An interrupted finish is retained after restart for explicit recovery.

After cleanup, results are prepared in the background. **Open results** opens
a named overview with notes, radio/antenna setup, operation states, clock sources,
diagnostic logs, and a link to the survey map. Empty sessions have an overview
explaining that no recordings were collected. **Surveys & exports → Survey
sessions** retains these runs, with result and ZIP links independently of recent
Activity history. Failed or interrupted result generation can be retried without
restarting radio operations.

Each recording's database identity is captured when its writer opens. Results
retain only the identities assigned to the named run, including retries within
that run; they exclude earlier and later recordings in the same radio database.
Original databases remain intact. Missing identities fail result generation
rather than falling back to the full database. Keep source recordings until
results are ready. The ZIP includes the session overview, manifest, summary,
logs, scoped recording database, map, and GIS exports when recordings exist.
Exported overviews link to sibling files and work from an extracted ZIP offline.

Radio connection management remains local to this workspace. Files from another
computer can still be imported and combined using the existing survey workflow;
named sessions do not coordinate remote radio starts or align their clocks.

## Radio connection status and recovery

Radio cards, Activity, and the Live view status strip use the same connection
status rules. **Recorders running** counts running recorder processes, including
ones trying to connect; inspect each radio's badge for its actual state.

| Status | Meaning and next action |
| --- | --- |
| Not connected | The radio has a saved setup but no active managed connection. Start recording or run a field preflight. |
| Connecting | The recorder is opening Bluetooth. Check power/pairing, move the radio nearby, and close other radio apps. |
| Waiting for telemetry | Bluetooth/device information was read; no complete sample has arrived yet. Keep the radio nearby and inspect operation details if it persists. |
| Recording | A fresh sample belongs to this recorder. GPS lock is shown separately; RSSI can be saved before mobile map points qualify. |
| Reconnecting | The recorder reported a request or connection failure and is performing its normal retry. The error stage and next attempt appear beside the radio. |
| Status stale / Telemetry stale | The producer report or latest sample is overdue. Check the radio and details; the UI does not infer a healthy connection from a running process. |
| Status unavailable | The workspace cannot be reached, or an older recorder has no current status evidence. Existing operations may still need checking. |
| Stopping / Verifying TX stop | A stop was requested. Cleanup has not yet completed. |
| Stopped | The managed operation ended. Saved telemetry is history. |
| Check radio | A prior operation did not complete known cleanup. Check the radio and process, then explicitly acknowledge it in Activity. |

Old RSSI and GPS values remain available but are muted and labeled **Last
recorded**. Data from before a recorder's launch cannot establish its live link.
A recent saved sample also does not override an explicit reconnecting state.
Samples remain in the recording when a connection drops.

**View operation** opens that operation's diagnostics; **View radio** returns
to its card. Audio and field operations link to their relevant controls. These
links never start a radio or retry RF transmission. Disabled audio controls show
why the selected radio is unavailable. Local preview and analysis can still run
with audio tools installed even if Bluetooth support is missing.

Managed recorders write a status report containing attempt, phase, retry time,
last error/stage, current sample, and recording identity. Direct CLI users can
request this through `start --status-output NEW_DIRECTORY`. Report freshness
allows for the configured connection/request timeouts. This does not change the
recorder's acquisition/retry policy or add another radio connection.

## Passive and active sweeps

Open **Field sweeps** and choose the sweep type before starting. New ungrouped
workflows default to Passive; choosing a sweep type or a saved session never
starts a radio. A named session keeps one sweep type for its lifetime. Create a
new session to change from passive to active or vice versa.

| | Passive sweep | Active sweep |
| --- | --- | --- |
| RF source | Whatever transmissions the radio receives on its configured channel | Scheduled 1 kHz test tones, with receive sampling between local tones |
| App transmission | No tones or PTT requests | Five-second tone every twenty seconds until halted |
| Start timing | Start sampling after connection; no minute alignment | Fixed :00/:20/:40; mobile :10/:30/:50, using the existing GPS/computer timing reference |
| Readiness | Control connection, current channel, recording destination, and GPS status | Control, destination, tone encoder, audio connection, and timing clock |
| Start action | Check passive readiness, then **Start passive sweep** | Check active readiness, then review and explicitly confirm RF transmission |
| Stop action | **Stop passive sweep** | **Halt automatic mode** |

Passive sweeps use the normal read-only recorder and its configured sampling
interval, transport, and GPS setting. They require Bluetooth support but no
audio codecs or Classic audio service. Configure the channel on the radio;
Passive does not scan or retune across frequencies. The app cannot attribute
arbitrary received transmissions to a particular transmitter. RF activity
observed during manual radio TX remains excluded from receive measurements.

The passive monitor shows current receive RSSI, position GPS, sample count, and
recorder/reconnection status. Tone countdowns, time-slot diagrams, RF review,
and completed-tone counters appear only for Active. Computer receipt timestamps
are used for passive samples; GPS fix timestamps remain separate. Neither sweep
records received audio to WAV; use Audio tests for that capture/analysis workflow.

A session's type is enforced by both launch and recorder paths. Passive sessions
cannot prepare a tone transmission, and active sessions cannot attach a passive
recorder. Readiness checks are matched to the selected type and still expire
after two minutes. **Finish survey** stops only that session's operations and
prepares its scoped results for either type. Named passive recorders disable
dashboard Reset to preserve the recording identities needed by those results;
finish the session and create another for a fresh run. Live view and Exit remain
available.

Session lists, result overviews, and maps identify Passive or Active. CSV adds a
`sweep_type` column, and GIS point properties and recording metadata retain the
same provenance. Combining different types is labeled Mixed. Older named field
sessions retain their original Active behavior. Legacy raw recordings without
sweep metadata remain Unclassified rather than being guessed as Passive.
