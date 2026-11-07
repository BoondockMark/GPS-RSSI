# GPS-RSSI

Collect measurements for mapping both directions of an RF repeater link around
La Habra Heights. Recording uses a **read-only Benlink Bluetooth diagnostic logger**.
It supports Benlink BLE and Bluetooth Classic RFCOMM, local SQLite storage,
JSONL replay, and CSV export. The recording commands never request PTT, send RF
packets, or change radio settings. Bluetooth requests themselves are transmitted to the radio.

Optional audio testing adds `tone`, `audio-services`, `audio-receive`, and `sinad`.
RF transmission requires **`tone --transmit --address ...` or the reviewed
Transmit test tone action in the workspace**; `tone` alone prepares files without
connecting to a radio. See [Bluetooth tones and SINAD](docs/sinad.md)
for installation, service discovery, bounded transmission, receive capture, analysis,
and physical-radio acceptance checks.

Read-only reports, HTML maps, and a local live dashboard are also available.
Offline time alignment combines mobile and fixed logs into a directional map.
The Kenwood ADC adapter and RF calibration are planned and not implemented.

Multiple transceivers can record concurrently with `fleet`. Portable `share`
snapshots and lossless `merge` preserve recording identities across computers.
`survey` creates a multi-transceiver comparison map with charts, filters, CSV,
GeoJSON, KML, GPX, and GeoPackage exports. See
[multiple transceivers, sharing, and GIS](docs/multi-transceiver.md) for setup
and the complete workflow. Sharing is file-based; live synchronization between
computers is not implemented.

## Open the visual workspace

After installation, double-click **`start-app.cmd`** on Windows or run
**`./start-app.sh`** on Linux. The local workspace opens in your browser with
**Radios**, **Live view**, **Surveys & exports**, and **Audio tests**. Add radios,
start/stop recorders, build survey maps, download GIS files, and run audio tests
without assembling terminal commands. Existing personal setups appear automatically.

Activity shows background progress, saved reports, downloads, and radio-stop
warnings. Closing a browser tab leaves recording running; use **Exit workspace**
to stop managed operations and save data. See [the workspace guide](docs/workspace.md)
for first setup, sharing, audio-test review, and recovery.

## Install

Use **Python 3.11 or later** and Git on Windows or Linux. Raspberry Pi OS
Bookworm 64-bit, Debian 12+, and Ubuntu 24.04+ are suitable Linux starting points.
Run the following from your GPS-RSSI checkout.

**Windows (Command Prompt):**

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[bluetooth]"
start-app.cmd
```

**Linux (terminal):**

```sh
sudo apt update
sudo apt install python3 python3-venv python3-pip git bluez rfkill
sudo systemctl enable --now bluetooth
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[bluetooth]"
./start-app.sh
```

The launchers use the checkout's virtual environment without activation. Create
that environment separately on each OS; do not copy `.venv` between computers.
The workspace provides discovery and both station roles. Direct station/scanner
launchers remain available for advanced use: `start-mobile`, `start-fixed`, and
`scan-radios`, with `.cmd` on Windows or `.sh` on Linux.

For Audio tests, install the additional dependencies once with
`python -m pip install -e ".[bluetooth,audio]"`, then restart the workspace.

See [Windows and Linux setup](docs/running.md) for Bluetooth preparation,
PowerShell, headless Raspberry Pi operation, and troubleshooting. The UV-PRO has
been tested by the user on Windows; physical-radio Linux testing is still pending.
Use double quotes as shown; do not add a trailing `\` to Windows commands.

## Command help

```bat
gps-rssi --help
gps-rssi workspace --help
gps-rssi record --help
gps-rssi replay --help
gps-rssi export --help
gps-rssi report --help
gps-rssi map --help
gps-rssi combine --help
gps-rssi serve --help
gps-rssi start --help
gps-rssi fleet --help
gps-rssi share --help
gps-rssi merge --help
gps-rssi survey --help
gps-rssi tone --help
gps-rssi audio-services --help
gps-rssi audio-receive --help
gps-rssi sinad --help
```

Top-level help lists every command and a quick start. Each command's help
describes its options, defaults, file behavior, and examples. `record` captures
live Bluetooth data, `replay` imports test frames, `export` writes CSV,
`report` summarizes logs, `map` creates an HTML measured-point map, and `serve`
follows the log in a local browser dashboard.

## Find and select a radio

On Windows, **double-click `scan-radios.cmd`**; on Linux, run
`./scan-radios.sh` from a terminal. With the virtual environment active, use:

```bat
gps-rssi scan
gps-rssi scan --select
gps-rssi scan --select --profile fixed
```

`scan` lists candidates without changing files. `--select` asks for a numbered
radio and a mobile/fixed setup (unless `--profile` is supplied), then saves the
selected BLE address and advertised name. Leave an answer blank or enter `q` to
cancel. `--timeout 20` allows a longer scan. For scripted inspection without
prompts or configuration changes, use `gps-rssi scan --json`.

The name filter recognizes **UV-PRO, UV-50PRO, VR-N76, and VR-N7600**, including
common spacing, case, and brand-prefix variations. A matching saved/template
address or advertised Benshi command-service UUID can also identify a candidate
when its name is missing. Unrelated devices are omitted from the candidate list.
Enable Bluetooth on the radio, keep it nearby, and close other control apps if
it does not appear. Scanning discovers advertisements only: it does not pair,
connect, verify radio commands, tune, or transmit.

**Advertised names are not verified hardware identities.** Your UV-50PRO can
advertise `VR-N7600`; the scanner shows that name separately from its fixed-site
setup hint. Service-only candidates have unknown model identity. The displayed
Bluetooth dBm is a discovery/proximity measurement, not the RF-link RSSI used in
coverage maps. A detected candidate still needs a normal logging test.

Selection updates only `[radio].address`, `[radio].transport` (to `ble`), and
`[radio].advertised_name`. Existing comments, station names, recording labels,
database paths, ports, and other settings are preserved. Update the recording
label yourself if you change to another physical model. Missing files are seeded
from the chosen profile. Existing files receive a timestamped, byte-for-byte
backup in `setup-backups/`; the new file is validated before atomic replacement.
Detected concurrent edits and validation/write failures do not replace the
original settings. Backups are not automatically deleted.

The scanner prints the saved file and the command to launch it. Selection affects
the **next launch**; it does not reconfigure an already-running logger. Custom
profiles can be targeted with `--config PATH`. The Windows scanner uses the
project virtual environment without manual activation and keeps its console
open to show the result.

If updating an existing installation, refresh the Bluetooth extras once so the
comment-preserving TOML editor is installed:

```bat
python -m pip install -e ".[bluetooth]"
```

## Advanced: direct mobile-station launcher

After installation, **double-click `start-mobile.cmd`** in this checkout on Windows.
It uses `.venv\Scripts\python.exe` directly, so you do not need to activate the
environment or remember recording/dashboard commands. It opens one console,
starts both services, and opens the dashboard in your browser. Installation is
still required once; the launcher does not install or update anything.

On Linux, run `./start-mobile.sh` from a terminal. It provides the same combined
logger and dashboard. Both launchers keep recording until you exit.

Alternatively, with the virtual environment active, run:

```bat
gps-rssi start
```

### Mobile and fixed-site setup profiles

The names describe station roles, so either setup can use a UV-PRO, UV-50PRO,
VR-N76, or VR-N7600. Use the scanner to select the radio for each role.
The supplied addresses and recording labels are initial choices for your
current radios; edit the recording label when changing models. Station IDs and
database names describe the mobile or fixed role.

Bluetooth addresses are stored in TOML setup files, not application code or CLI
examples. The only checked-in copies of the supplied addresses are in the two
bootstrap templates under `src/gps_rssi/profiles/`. On first use, the selected
template is copied to a personal file beside the launcher. Edit `[radio].address`
in that personal file to change a radio; existing files are never overwritten.

| Profile | Personal settings | Windows / Linux launcher | Role | GPS | Database | Port |
|---|---|---|---|---|---|---|
| Mobile | `mobile.toml` | `start-mobile.cmd` / `./start-mobile.sh` | Downlink receiver | Enabled | `data/mobile.sqlite` | 8765 |
| Fixed | `fixed.toml` | `start-fixed.cmd` / `./start-fixed.sh` | Uplink receiver | Disabled | `data/fixed.sqlite` | 8766 |

```bat
gps-rssi start
gps-rssi start --profile fixed
```

Use one command on each station's computer. Separate ports also allow bench
testing both profiles on one computer. You may choose another personal file:

```bat
gps-rssi start --profile fixed --config "site-settings.toml"
```

`--profile` selects initial defaults and the default filename; values already in
the file take precedence. Both default personal files are ignored by Git.
`recording.role` is `mobile` or `fixed`. Partial settings inherit omitted defaults.
The fixed profile logs RSSI without GPS and identifies measurements as uplink.
Its dashboard shows RSSI and radio state, but uplink points cannot be plotted
until the fixed log is paired with the mobile's GPS. The fixed-site UV-50PRO
profile is distinct from NXR-1800 measurements. Bluetooth compatibility still
needs validation on that physical radio. This change does not key either radio,
schedule transmissions, tune channels, or pair logs automatically.

For the default mobile profile, first launch creates a personal `mobile.toml`.
Its defaults are:

- The UV-PRO address from `[radio].address` in the mobile setup template, using BLE.
- GPS enabled, station `mobile`, and database `data/mobile.sqlite`.
- A 60-second connection timeout, one-second sample interval, and automatic retries.
- The La Habra Heights dashboard on port 8765, opening automatically in a browser.

Edit `mobile.toml` in a text editor to change the radio, data file, port, browser
preference, or other settings. Existing settings are never overwritten. The
personal file is ignored by Git, so updating the project does not replace it.
Partial files inherit omitted defaults; unknown settings or invalid values fail
with an error. Relative database paths are resolved beside the settings file.
The Windows and Linux launchers always use the checkout's personal settings, even when
started from another working directory. Run `gps-rssi start` from the checkout,
or supply `--config` explicitly when using another terminal directory.

```bat
gps-rssi start --config "C:\Users\mjhug\Documents\Projects\GPS-RSSI\mobile.toml"
gps-rssi start --no-browser
```

Use **Exit app** in the dashboard, or press **Ctrl-C once** in the console, to
stop both services and disconnect the radio. Closing the browser tab does not stop recording. Leave the console open
while collecting data. If the browser cannot open automatically, the address is
printed in the console and recording continues. Existing measurements remain in
the database; each launch appends a new recording session.

Stop any older `record`/`serve` windows before using the combined launcher. Two
`start` instances cannot use the same database simultaneously; an OS lock is
released automatically on exit or crash. The small `.app.lock` file may remain
beside the database and should not be deleted while an instance is running.
The guard does not coordinate separate `record` commands or a second
profile using the same radio with a different database. A busy dashboard port
fails before connecting the radio and explains how to change it. If either
combined service fails, the other is stopped and the error is shown.

The separate `record` and `serve` commands below remain available for advanced
use. The combined launcher keeps Bluetooth on the main event loop and the HTTP
server in a managed thread; it does not leave child logger processes behind.
Advanced `record` examples below use `RADIO_ADDRESS`: substitute `[radio].address`
from your personal setup file. Daily `start` launches read it directly, so no
address needs to be entered on the command line.

### Reset and exit controls

When using `gps-rssi start` or `start-mobile.cmd`, the dashboard offers:

- **Exit app:** confirm, then let the current radio operation finish cancellation
  and disconnect cleanup. Writes are committed, a stopped event is saved, and the
  logger and local server stop. The tab displays a final stopped message and stops
  polling; you can close it. Relaunch normally for the next session. Cleanup can
  take up to the configured command timeout. Closing only the tab still leaves
  recording running.
- **Reset database:** the confirmation names the active database and explicitly
  covers **all recorded samples, sessions, and events in it**, including other
  stations and replay data. Collection pauses and the radio disconnects. A
  verified SQLite backup is saved beside the database under `backups/`, then
  the recording tables are cleared in one transaction and a new session starts.
  The radio reconnects, map points/counters clear, and recording resumes. Your
  settings, planning overlay, and other database files are unchanged.

Backup names contain a UTC timestamp and unique suffix; existing backups are
never overwritten. The dashboard and console show the full backup path. You can
inspect a backup directly with SQLite tools or `gps-rssi report --db PATH`.
No backup retention cleanup is automatic, so keep enough disk space for the
complete database copy. Logical reset may not shrink the current file; SQLite
reuses its free pages.

Reset uses SQLite's backup API, including committed WAL data, rather than copying
only the main file. The current database stays open and is not replaced, so
readers can finish an old snapshot. If backup creation/verification or the clear
transaction fails, original records remain, the error is displayed, and recording
resumes on the original session. The backup from a failed clear may remain as an
additional copy. Do not run independent writers against the same database
while operating the combined app.

Only one control operation is accepted at a time. Keep the page open to see its
result; a long-running reset continues even if a browser request times out.
Reloading the dashboard reconnects to an in-progress operation. Browser requests
require the per-launch capability from the same-origin dashboard, and reset also
requires explicit confirmation. GET requests cannot trigger these operations.
The separate `serve` command remains read-only and hides the controls because it
does not own the logger in another process.

For replay, export, and tests without Bluetooth dependencies, use
`python -m pip install -e .` instead. No API keys or Internet connection are
required during logging. Bluetooth operation needs a local adapter, BlueZ and a
running Bluetooth service on Linux. Pair/trust the radio using your operating
system's Bluetooth tools (for example `bluetoothctl`). Stop other radio-control
apps during the initial test. Run `record` on the computer beside the radio.

## Try without hardware

The example contains **synthetic measurements**, not a coverage survey: 25
distinct map locations around La Habra Heights with relative RSSI spanning 0–100.
They illustrate marker colors and dashed outlines for closed squelch; their
positions and signal strengths do not represent an actual route or RF conditions.
Two additional samples demonstrate transmit and GPS-loss exclusions.

```sh
gps-rssi replay examples/mobile.jsonl --db data/demo.sqlite --station demo-mobile
gps-rssi export --db data/demo.sqlite --output data/demo.csv
python -m unittest discover -s tests -v
```

Each replay appends a distinct session; repeating it duplicates the input as a
new session. Export refuses to overwrite an existing file. The `source` column
distinguishes replay from live Bluetooth data.

## Inspect a recording

Run a report on your actual log (these commands work in Windows Command Prompt):

```bat
gps-rssi report --db data/mobile.sqlite
gps-rssi report --db data/mobile.sqlite --station mobile --json
```

The report lists sessions, sample times, RX/TX/squelch counts, receive-only RSSI
statistics, GPS quality, event counts, and connection errors by operation. Empty
sessions are included, so a session that never connected is still visible.
GPS and connection errors can describe the same incident and are counted
separately. Radio-state counts also overlap.

Reports include all sources by default, with each session's source identified.
Use `--source live` to exclude replay, or `--source replay` for synthetic input.
Use `--station NAME` or `--session FULL_ID` to select one station or recording.
Keep radio models/configurations separate when comparing relative RSSI values.

## Map mobile reception

```bat
gps-rssi map --db data/mobile.sqlite --output data/mobile-map.html
```

Open the generated HTML file in a browser. The map shows individual **mobile
receive measurements**, colored by relative RSSI, grouped into layers by station
and session. It does not interpolate coverage between points, identify the
transmitting station, or establish voice quality or two-way coverage. Zero RSSI
and closed-squelch receive samples are retained; closed squelch is shown with a
dashed outline. Idle/TX and fixed-role samples are excluded.

Maps select **live data only by default**. To inspect the synthetic example:

```bat
gps-rssi map --db data/demo.sqlite --source replay --output data/demo-map.html
```

`--source all` includes live and replay in separately labeled layers, with larger
purple outlines on replay points. Output files are never overwritten; choose a
new filename to regenerate a map. The output directory must already exist.
Both analysis commands open SQLite read-only and can read a consistent snapshot
while the logger runs. An existing HTML map does not update as logging continues.

GPS qualification requires a reported lock, finite coordinates within latitude/
longitude bounds, timezone-aware sample/fix timestamps, and a finite nonnegative
reported accuracy. Defaults exclude fixes with an absolute sample/fix time
difference over **10 seconds** or a reported accuracy radius over **100 meters**.
Missing accuracy is excluded. These limits can be changed with `--max-gps-age`
and `--max-gps-accuracy` on either command. Age is relative to the **measurement
time**, so historical surveys remain usable; fixes too far in the future also
fail the time check. Keep computer and radio clocks synchronized. The radio's
accuracy estimate is a filter input, not a guarantee of actual position error.

The CLI and HTML list exclusion counts. If no points qualify, an explanatory
HTML page is created instead of showing an invented location. Older replay
frames without `status_received_at` may fail the time check because their import
time was used as sample time; provide the original timestamp when available.

Measurement data is embedded in the HTML. The interactive map requires Internet
access to Leaflet 1.9.4 from `unpkg.com` and OpenStreetMap tiles from
`tile.openstreetmap.org`. A measurement table remains available when these
services cannot load. No Bluetooth connection or Python server is needed to view
the file. Treat exported HTML as location data when sharing it.

## Live dashboard: La Habra Heights

Keep the logger running in one terminal:

```bat
gps-rssi record --address RADIO_ADDRESS --station mobile --db data/mobile.sqlite --gps --connect-timeout 60
```

In a second terminal, activate the same virtual environment and start the viewer:

```bat
.venv\Scripts\activate
gps-rssi serve --db data/mobile.sqlite
```

Open the address printed by the server (default `http://127.0.0.1:8765`) on that
computer. Start the logger first so the database exists. Use `--port 8766` if
8765 is already occupied. Ctrl-C stops the viewer; it does not stop the logger.
The server binds only to loopback and does not offer remote/LAN access.

The viewer fetches newly saved samples every two seconds without reloading the
page or resetting the map view. It shows the latest loaded RSSI, RX/TX and GPS-lock
state, sample age, last recorded link event, exclusions, and station/session
layers. "Recent telemetry" means a sample is no older than 15 seconds; a recorded
connection event is not proof that the radio is still connected. Pausing updates
does not pause recording. Enable **Follow newest point** to pan as measurements
arrive, or use **Fit operating area** / **Fit measurements** to change the view.

Select **Maximize map** to fill the browser window with the map. Description
panels and the measurement table are hidden, and the controls move into a bar
across the bottom. Buttons wrap on narrow screens, and the bar can scroll on very
small windows. Important errors and reset/exit results remain visible as compact
notices over the map. Select **Restore map** or press **Escape** to return to the
normal dashboard. The current center, zoom, selected layers, and live polling are
retained. Browser resizing also refreshes the map dimensions; maximizing does not
pause or otherwise change recording. This mode fills the browser's content area
without requesting operating-system fullscreen permission.

Initial load checks the newest 5,000 selected raw samples. Thereafter it fetches
new samples incrementally in batches of up to 1,000. The map retains at most
5,000 eligible points and the table shows at most 500 of those. Older samples
remain in SQLite for `report`, `map`, and `export`. Exclusion counts describe the
loaded window plus subsequent samples, not the entire survey. Change retention
with `--max-points N` and polling with `--refresh SECONDS`. A read failure keeps
the displayed data and retries. A replacement database with different sessions
resets the loaded window.

The same station/session/source and GPS-quality filters are available as on
`map`. Live data is selected by default. For a synthetic demonstration, first
replay `examples/mobile.jsonl`, then run:

```bat
gps-rssi serve --db data/demo.sqlite --source replay
```

Click **Fit measurements** to zoom to the sample points. If you previously
imported the older one-point example, replay the updated file into a fresh
database (for example, `data/demo-expanded.sqlite`) and use that same database
with `serve` or `map`. Existing databases do not change when the example file is
updated. Replaying into an existing database appends another session.

The initial view shows **La Habra Heights plus a 0.5-mile outward buffer**
(804.672 meters) from the **city polygon**, not from its center. Measurements
outside that planning area remain visible and are still recorded. Use
`--area none` to hide the overlay. The buffer is a planning aid, not a promise of
radio coverage or a survey of legal boundaries.

The city outline is the Census Bureau's **2022, 1:500,000 generalized place
boundary**, GEOID `0639304`, distributed through
[Census CitySDK](https://github.com/uscensusbureau/citysdk/blob/master/v2/GeoJSON/500k/2022/06/place.json).
The packaged GeoJSON records the source hash and projection. A metric buffer was
computed in UTM zone 11N (`EPSG:32611`) and transformed back to longitude/latitude.
Generalization, data age, and projection scale limit its precision; it is not
necessarily the current municipal boundary. It is bundled, so no geocoding or
city-boundary download occurs during operation.

The dashboard serves **Leaflet 1.9.4 locally**, under its included BSD license.
Street tiles still come from `https://tile.openstreetmap.org/{z}/{x}/{y}.png`,
with OpenStreetMap attribution. Without Internet access, points and the boundary
can still display on a blank background, and the measurement table works.
No tile prefetching or offline bulk downloads are performed. Standalone `map`
exports retain their CDN-based Leaflet loading and measured-point viewport.

Validation commands:

```sh
python -m unittest discover -s tests -v
node tests/dashboard.test.cjs
```

Node is optional and only needed for the frontend behavior tests, not to run the
dashboard. Python tests include real loopback HTTP requests while samples are
appended. Frontend tests cover polling, deduplication, retention, errors, pause/
resume, follow, database replacement, and the table fallback in a DOM/Leaflet
harness. Full visual browser validation remains a separate check.

For maintainers, `scripts/build_planning_area.py` rebuilds the overlay from the source
California GeoJSON. It requires `shapely` and `pyproj` in a separate maintenance
environment; neither is a runtime dependency. The output path must not already
exist. Runtime package resources include the boundary, frontend, Leaflet, and
Leaflet's license and standard images.

## First radio test

Start with one channel selected and scanning/dual watch disabled manually.
Your UV-PRO's Bluetooth address is included below. Commands are on single lines
so they can be pasted into Windows Command Prompt:

```bat
gps-rssi record --address RADIO_ADDRESS --transport ble --station mobile --db data/mobile.sqlite --label "UV-PRO; firmware and antenna notes" --max-samples 30
```

For the next GPS test, take the radio outdoors and wait for a fix:

```bat
gps-rssi record --address RADIO_ADDRESS --station mobile --db data/mobile.sqlite --gps --max-samples 30
gps-rssi export --db data/mobile.sqlite --output data/mobile.csv
```

BLE is the default. For Bluetooth Classic, discover the radio's **command**
service RFCOMM channel and pass it explicitly. Do not substitute a KISS/TNC or
audio service. For example, **only if discovery reports command channel 1**:

```bat
gps-rssi record --address RADIO_ADDRESS --transport rfcomm --rfcomm-channel 1 --station mobile --db data/mobile-rfcomm.sqlite --gps --interval 1 --timeout 10
```

Omit `--max-samples` to log until Ctrl-C. It counts successful status samples,
not connection attempts; a disconnected radio is retried until stopped.
`--reconnect-delay` defaults to 5 seconds. `--connect-timeout` gives Bluetooth
discovery and connection setup 30 seconds by default. `--timeout` limits each
radio command and disconnect cleanup to 10 seconds by default.
Samples commit individually; interruption retains completed samples.
Connection diagnostics and device information are saved in the `events` table.
The software does not change system clock, radio firmware, or radio configuration.

`--gps` requests a position only when status reports GPS lock. Position retrieval
failure retains the status measurement without coordinates and reconnects with
fresh request state. Leave GPS off for the first RSSI-only test. Device/firmware
support for GPS and extended status must be confirmed on each radio. Benlink
lists UV-PRO; HTCommander lists UV-PRO and UV-50Pro. The user has confirmed initial
UV-PRO logging and GPS work; the other physical radios still need validation.

If an older Benlink was already installed and the logger reports a missing GPS
API, run `python -m pip install --force-reinstall -e ".[bluetooth]"` in this
virtual environment. The upstream source revision shares its version number
with the older PyPI release, so a regular install can retain the older code.

## Intermittent Bluetooth connections

The logger retries failed connections automatically with a fresh connection
object. BLE cleanup disconnects directly through Bleak, which also stops
notifications. This avoids the pinned Benlink cleanup path where a failed
notification-stop request could prevent disconnecting the device.

Console messages identify each stage:

- `Connecting`: discovery/connection setup is in progress.
- `Bluetooth connected; reading device information`: the link opened, but the
  radio command interface has not yet been confirmed responsive.
- `Connected; device info recorded in SQLite`: the radio answered a command.
- `connect`, `device_info`, `status`, or `position` in a failure message identifies
  the operation that failed. Timeout messages include the configured duration.
- `Disconnect cleanup failed`: the old connection might not have been released.

Errors, their stages, attempt numbers, and disconnect results are saved in the
`events` table. Previously committed samples remain available across retries.
Connection success does not guarantee that the next command will succeed.

Close phone control apps and other logger instances so they do not compete for
the radio. Keep the radio nearby for the initial test. If discovery/setup is
slow, try a longer connection timeout:

```bat
gps-rssi record --address RADIO_ADDRESS --station mobile --db data/mobile.sqlite --gps --connect-timeout 60 --max-samples 30
```

Let it retry rather than immediately restarting it. To stop, press Ctrl-C once
and allow cleanup to finish before launching another instance. If failures
continue, retain the console messages showing the failed stage; Windows adapter,
radio firmware, interference, and competing clients still need hardware diagnosis.
These changes do not establish which of those caused a particular field failure.

## Measurement meaning

- Benlink RF strength is stored as `benshi_relative_0_100`, **not dBm or calibrated
  percent signal power**. Benlink scales a four-bit field (16 levels) by 100/15.
  This is radio RF strength, not Bluetooth RSSI. Decoded replies are retained
  as JSON; these are not original wire bytes.
- Mobile receive samples are labeled `downlink`; fixed-role receive samples
  are labeled `uplink`. Idle/TX samples remain `unqualified`, with their state
  retained. These labels describe receiver direction, not confirmed source
  identity or successful two-way communication. Use `combine` for offline time
  association with the opposite station's transmit observations.
- `--role fixed` labels a supported radio used as a fixed-site monitor. It does
  **not** enable an NXR-1800 interface or make a monitor's reading equivalent to
  the repeater receiver's reading.
- Squelch and receive flags are retained separately; low/zero RSSI is not dropped.
  RSSI alone does not establish usable voice reception.
- Host UTC status-request and receipt times are recorded, along with the radio's
  GPS timestamp and accuracy in the decoded payload. GPS is queried after status,
  so those observations are not simultaneous. No previous fix is carried forward.
  Keep host clocks synchronized before comparing logs across stations. Inspect
  GPS age and accuracy before mapping; the logger does not yet filter stale fixes.
- Channel settings are read after each status sample using Benlink's read-only
  channel API. SQLite and CSV retain `frequency_hz`, `rx_frequency_hz`, and
  `tx_frequency_hz` as integer Hz. `frequency_hz` is the configured RX frequency
  while receiving/idle and TX frequency while transmitting (RX in talk-around).
  These are channel settings for the reported channel, not measured RF frequency;
  status and channel reads are separate, so scanning/dual-watch or changes between
  reads may not identify the exact frequency of a brief signal. Both configured
  frequencies and the raw channel response are retained for inspection.
  Failed channel reads retain the sample with unknown frequency, record a
  `frequency_error`, and reconnect. No previous frequency is carried forward.
  Existing databases upgrade additively when opened for recording; older rows
  stay unknown. Read-only exports and mixed old/new survey merges also support
  older databases without changing them. Replay JSONL may include a `channel`
  object with matching `channel_id`, `rx_freq`/`tx_freq` in **MHz**, and optional
  boolean `talk_around`; absent frequency settings remain unknown.
- Record model, firmware, antenna, mounting, bandwidth, and power
  settings in `--label`. The status channel ID is not a frequency. Compare the
  two BTECH models separately until their strength scales are characterized.

## Bench acceptance checklist

1. Record device information and 30 status samples from each BTECH model.
2. With a known received signal, check strength changes, RX/squelch flags, and
   idle behavior. Check TX flags during a separately controlled transmission.
3. Enable GPS and confirm coordinates, timestamp, lock loss, and accuracy.
4. Disconnect/reconnect Bluetooth; confirm earlier samples remain and a connection
   error appears in `events`.
5. Export CSV and inspect values before a stationary two-station experiment.

Software tests exercise polling, timeouts, reconnection, cancellation, validation,
storage, replay, and export with fake radios. They do not establish RF calibration,
Bluetooth interoperability, firmware compatibility, or real sampling rate.

## Next hardware adapter: Kenwood NXR-1800

The supplied service manual identifies CONTROL I/O pin 1 as a 0–3.3 V RSSI
output **after the TL501/TL502 modification** (section 2.4.2, page 1-12; pin
specification page 1-27). Pin 17 provides squelch control with a roughly 5 V
pull-up (page 1-28). A Raspberry Pi requires an external ADC for RSSI and suitable
interfacing for the squelch signal. Grounding, loading, protection, polarity, and
voltage-to-dBm calibration need bench verification before use.

## Protocol references

- [Benlink](https://github.com/khusmann/benlink), pinned to commit
  `c4b9d22112402ea193d376f412bc028d1778fdb4`. The PyPI 0.1.1 release lacks
  `get_position`; this source revision includes it.
- [Benlink RSSI encoding](https://github.com/khusmann/benlink/blob/main/src/benlink/protocol/command/status.py).
- [HTCommander](https://github.com/Ylianst/HTCommander), including UV-50Pro support.
- [HTCommander command protocol](https://github.com/Ylianst/HTCommander/blob/HEAD/docs/blogs/radio-command-protocol.md).

## Combine mobile and fixed logs

After recording on both computers, stop the apps and copy the fixed database to
`data/fixed.sqlite` on the computer holding `data/mobile.sqlite`. Then run:

```bat
gps-rssi combine --mobile-db data/mobile.sqlite --fixed-db data/fixed.sqlite --output data/combined
```

Open `data/combined/map.html`. It has separate fixed-to-mobile and mobile-to-fixed
layers, using the mobile GPS position for both. Only time-associated points are
shown initially; location-only unconfirmed results have optional layers. The
CSV retains all receive observations and rejection reasons. `summary.json`
records input paths, selected sessions, thresholds, and clock corrections.
Existing output folders are refused and input databases are read-only.

Use `gps-rssi combine --help` for station/session selection, GPS and timing limits,
and explicit host-clock offsets. Keep both computers synchronized to UTC.
Time association supports the matching; it does not identify another transmitter
or prove two-way voice coverage. See [time alignment](docs/time-alignment.md)
for matching rules and the bench test needed to validate limits on your radios.

## Visualize signal and match quality

The live dashboard and combined survey use the same purple-to-yellow scale:
0 is weaker and 100 is stronger. This is receiver-relative RSSI, not a calibrated
coverage or voice-quality rating. Missing measurements are never plotted as zero.

Both views include receive-only timelines, distributions, sample counts, median,
10th percentile, and observed range. Statistics stay separate by direction and
recording session. Select a timeline point or map marker to inspect a measurement;
timeline selection pans to its map position when a qualified GPS location exists.
The 10th percentile helps inspect the weaker part of the observed readings; it
is not a reception threshold or a percentage of land covered.

The live charts use a bounded telemetry window, so the fixed station can show
receive history without GPS. TX and idle samples are excluded from these signal
statistics. Pause updates, database reset, clean exit, and map maximize retain
their existing behavior. Charts follow the newest retained telemetry window,
while map points have their own retained point limit; earlier data stays in SQLite.

In a combined survey, **Direction** and **Match quality** filter the map, charts,
counts, and table together. Associated results are shown initially; unconfirmed
and unmatched observations can be inspected explicitly. **Fit visible
measurements** changes the map view on demand. Filters do not change the CSV or
source databases. Receiver model/antenna notes remain in the recording label.

Charts use local JavaScript and SVG without an external chart service. Large
series show at most 602 actual observations per timeline, retaining weakest and
strongest readings in time buckets; statistics and histograms use all filtered
observations. No line bridges missing data or implies coverage between points.
On narrow screens, charts scroll horizontally so their labels remain readable.
Combined HTML embeds its chart code/styles, so charts and the results table work
offline even if Leaflet or street tiles cannot load. A previously exported HTML
file must be regenerated to get these visualization improvements.

## Architecture

See [the architecture guide](docs/architecture.md) for module responsibilities,
dependency direction, resource ownership, and the launcher structure.

The [time-alignment guide](docs/time-alignment.md) describes combining both logs
and checking opposite-station transmit observations.

### Review survey observations before export

Regenerate a `gps-rssi map` HTML file or open a newly generated `combine` bundle's
`map.html`. Under **Review before export**, enable **Select points on map** and
click errant observations, or select their table checkboxes. Selected markers
have red outlines. **Select all visible** follows the current combined-map
filters; changing filters clears selections that become hidden.

**Remove selected** excludes observations from the map, table, charts, statistics,
and cleaned export. **Undo removal** restores the most recent batch, including
when every point was removed. Review controls and table selection also work
without Leaflet. **Export cleaned CSV** downloads all retained observations,
including those hidden by display filters. Combined exports retain the original
CSV columns, association status, and rejection reasons. Mobile-map exports contain
the qualified plotted measurements, rather than every raw sample in SQLite.

Review edits last only in the current browser tab: download the cleaned CSV
before closing or reloading. Source databases, CLI `export`, and the bundle's
original `measurements.csv` and `summary.json` remain unchanged. Previously
exported HTML must be regenerated to get the review controls.
