# GPS-RSSI

Collect measurements for mapping both directions of an RF repeater link around
La Habra Heights. This first milestone is a **read-only BTECH diagnostic logger**.
It supports Benlink BLE and Bluetooth Classic RFCOMM, local SQLite storage,
JSONL replay, and CSV export. It never requests PTT, sends RF packets, or changes
radio settings. Bluetooth requests themselves are transmitted to the radio.

Read-only reports, HTML maps, and a local live dashboard are also available.
The Kenwood ADC adapter, event pairing between stations, and calibration are
planned; they are not implemented in this milestone.

## Install

Python 3.11 or later and Git on Linux are the initial target, including Raspberry Pi OS
64-bit and mini-PCs. Start from the existing checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[bluetooth]"
gps-rssi --help
```

On Windows Command Prompt, activate with `.venv\Scripts\activate` instead of
`. .venv/bin/activate`. Use double quotes as shown; do not add a trailing `\`
to commands. The UV-PRO has been tested by the user on Windows.

## Command help

```bat
gps-rssi --help
gps-rssi record --help
gps-rssi replay --help
gps-rssi export --help
gps-rssi report --help
gps-rssi map --help
gps-rssi serve --help
gps-rssi start --help
```

Top-level help lists every command and a quick start. Each command's help
describes its options, defaults, file behavior, and examples. `record` captures
live Bluetooth data, `replay` imports test frames, `export` writes CSV,
`report` summarizes logs, `map` creates an HTML measured-point map, and `serve`
follows the log in a local browser dashboard.

## Daily use: one launch for your UV-PRO

After installation, **double-click `start-uvpro.cmd`** in this checkout on Windows.
It uses `.venv\Scripts\python.exe` directly, so you do not need to activate the
environment or remember recording/dashboard commands. It opens one console,
starts both services, and opens the dashboard in your browser. Installation is
still required once; the launcher does not install or update anything.

Alternatively, with the virtual environment active, run:

```bat
gps-rssi start
```

The first launch creates a personal `uvpro.toml` settings file. Defaults are:

- Your UV-PRO: `38:D2:00:00:F3:34`, using BLE.
- GPS enabled, station `mobile-uvpro`, and database `data/uvpro-gps.sqlite`.
- A 60-second connection timeout, one-second sample interval, and automatic retries.
- The La Habra Heights dashboard on port 8765, opening automatically in a browser.

Edit `uvpro.toml` in a text editor to change the radio, data file, port, browser
preference, or other settings. Existing settings are never overwritten. The
personal file is ignored by Git, so updating the project does not replace it.
Partial files inherit omitted defaults; unknown settings or invalid values fail
with an error. Relative database paths are resolved beside the settings file.
The Windows launcher always uses the checkout's personal settings, even when
started from another working directory. Run `gps-rssi start` from the checkout,
or supply `--config` explicitly when using another terminal directory.

```bat
gps-rssi start --config "C:\Users\mjhug\Documents\Projects\GPS-RSSI\uvpro.toml"
gps-rssi start --no-browser
```

Press **Ctrl-C once** in the console to stop both services and disconnect the
radio. Closing the browser tab does not stop recording. Leave the console open
while collecting data. If the browser cannot open automatically, the address is
printed in the console and recording continues. Existing measurements remain in
the database; each launch appends a new recording session.

Stop any older `record`/`serve` windows before using the combined launcher. Two
`start` instances cannot use the same database simultaneously; an OS lock is
released automatically on exit or crash. The small `.app.lock` file may remain
beside the database and should not be deleted while an instance is running.
The guard does not coordinate separate legacy `record` commands or a second
profile using the same radio with a different database. A busy dashboard port
fails before connecting the radio and explains how to change it. If either
combined service fails, the other is stopped and the error is shown.

The separate `record` and `serve` commands below remain available for advanced
use. The combined launcher keeps Bluetooth on the main event loop and the HTTP
server in a managed thread; it does not leave child logger processes behind.
The examples below use your UV-PRO address, `38:D2:00:00:F3:34`; other radios
will have their own addresses. `record` requires an explicit address; `start`
uses your address as the initial personal-profile default.

For replay, export, and tests without Bluetooth dependencies, use
`python -m pip install -e .` instead. No API keys or Internet connection are
required during logging. Bluetooth operation needs a local adapter, BlueZ and a
running Bluetooth service on Linux. Pair/trust the radio using your operating
system's Bluetooth tools (for example `bluetoothctl`). Stop other radio-control
apps during the initial test. Run `record` on the computer beside the radio.

## Try without hardware

The example contains **synthetic measurements**, not a coverage survey:

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
gps-rssi report --db data/uvpro-gps.sqlite
gps-rssi report --db data/uvpro-gps.sqlite --station mobile-uvpro --json
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
gps-rssi map --db data/uvpro-gps.sqlite --output data/uvpro-map.html
```

Open the generated HTML file in a browser. The map shows individual **mobile
receive measurements**, colored by relative RSSI, grouped into layers by station
and session. It does not interpolate coverage between points, identify the
transmitting station, or establish voice quality or two-way coverage. Zero RSSI
and closed-squelch receive samples are retained; closed squelch is shown with a
dashed outline. Idle/TX and repeater-role samples are excluded.

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
gps-rssi record --address 38:D2:00:00:F3:34 --station mobile-uvpro --db data/uvpro-gps.sqlite --gps --connect-timeout 60
```

In a second terminal, activate the same virtual environment and start the viewer:

```bat
.venv\Scripts\activate
gps-rssi serve --db data/uvpro-gps.sqlite
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

For maintainers, `scripts/build_area.py` rebuilds the overlay from the source
California GeoJSON. It requires `shapely` and `pyproj` in a separate maintenance
environment; neither is a runtime dependency. The output path must not already
exist. Runtime package resources include the boundary, frontend, Leaflet, and
Leaflet's license and standard images.

## First radio test

Start with one channel selected and scanning/dual watch disabled manually.
Your UV-PRO's Bluetooth address is included below. Commands are on single lines
so they can be pasted into Windows Command Prompt:

```bat
gps-rssi record --address 38:D2:00:00:F3:34 --transport ble --station mobile-uvpro --db data/uvpro.sqlite --label "UV-PRO; firmware and antenna notes" --max-samples 30
```

For the next GPS test, take the radio outdoors and wait for a fix:

```bat
gps-rssi record --address 38:D2:00:00:F3:34 --station mobile-uvpro --db data/uvpro-gps.sqlite --gps --max-samples 30
gps-rssi export --db data/uvpro-gps.sqlite --output data/uvpro-gps.csv
```

BLE is the default. For Bluetooth Classic, discover the radio's **command**
service RFCOMM channel and pass it explicitly. Do not substitute a KISS/TNC or
audio service. For example, **only if discovery reports command channel 1**:

```bat
gps-rssi record --address 38:D2:00:00:F3:34 --transport rfcomm --rfcomm-channel 1 --station mobile-uvpro --db data/uvpro-rfcomm.sqlite --gps --interval 1 --timeout 10
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
UV-PRO logging works; GPS and UV-50Pro compatibility still need validation.

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
gps-rssi record --address 38:D2:00:00:F3:34 --station mobile-uvpro --db data/uvpro-gps.sqlite --gps --connect-timeout 60 --max-samples 30
```

Let it retry rather than immediately restarting it. To stop, press Ctrl-C once
and allow cleanup to finish before launching another instance. If failures
continue, retain the console messages showing the failed stage; Windows adapter,
radio firmware, interference, and competing clients still need hardware diagnosis.
These changes do not establish which of those caused a particular field failure.

## Measurement meaning

- BTECH strength is stored as `benshi_relative_0_100`, **not dBm or calibrated
  percent signal power**. Benlink scales a four-bit field (16 levels) by 100/15.
  This is radio RF strength, not Bluetooth RSSI. Decoded replies are retained
  as JSON; these are not original wire bytes.
- Mobile receive samples are labeled `downlink`; repeater-role receive samples
  are labeled `uplink`. Idle/TX samples remain `unqualified`, with their state
  retained. These labels describe receiver direction, not confirmed source
  identity or successful two-way communication. No event pairing is done yet.
- `--role repeater` changes labeling for a BTECH used as a site monitor. It does
  **not** enable an NXR-1800 interface or make a monitor's reading equivalent to
  the repeater receiver's reading.
- Squelch and receive flags are retained separately; low/zero RSSI is not dropped.
  RSSI alone does not establish usable voice reception.
- Host UTC status-request and receipt times are recorded, along with the radio's
  GPS timestamp and accuracy in the decoded payload. GPS is queried after status,
  so those observations are not simultaneous. No previous fix is carried forward.
  Keep host clocks synchronized before comparing logs across stations. Inspect
  GPS age and accuracy before mapping; the logger does not yet filter stale fixes.
- Record model, firmware, antenna, mounting, frequency, bandwidth, and power
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
