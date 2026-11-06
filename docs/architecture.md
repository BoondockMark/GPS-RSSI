# Project architecture

GPS-RSSI is one Python package with separate command, configuration, acquisition,
persistence, measurement, and presentation responsibilities. Windows and Linux launchers
are convenience entry points into the same CLI.

## Responsibilities

| Module or directory | Owns |
|---|---|
| `cli.py` | Argument parsing, help, command dispatch, and command-level input/output |
| `scan_cli.py` | Candidate display, interactive selection, and scanner workflow messages |
| `settings.py` | Profile defaults, personal TOML loading/validation, setup hints, and backed-up setup edits |
| `locking.py` | Cross-platform OS file locks; no knowledge of radio or dashboard lifecycle |
| `app.py` | Composition and supervision of the collector and HTTP server; start/reset/exit lifecycle |
| `controls.py` | Thread-safe lifecycle requests, per-launch capabilities, operation status, and shutdown acknowledgement |
| `discovery.py` | Read-only BLE discovery and candidate classification; no prompts or configuration writes |
| `benlink_radio.py` | Read-only radio acquisition, polling, command timeouts, and reconnect handling |
| `benlink_ble.py` | The narrowly scoped cleanup adaptation for the pinned Benlink library |
| `storage.py` | SQLite recording, consistent read snapshots, CSV export, and transactional backup/reset |
| `measurements.py` | Pure timestamp, GPS-quality, and receive-point qualification rules |
| `alignment.py` | Pure time association, GPS-track qualification, continuity segments, and TX evidence |
| `combine.py` | Read-only station/session input selection and new CSV/JSON/HTML survey bundles |
| `fleet.py` | Multi-transceiver setup preflight and supervision of independent recorder processes |
| `sharing.py` | Consistent SQLite snapshots and transactional merging with original sample/event provenance |
| `gis.py` | WGS84 GeoJSON, KML, GPX, and GeoPackage point encodings without external GIS dependencies |
| `survey.py` | Multi-transceiver bundle composition, source selection, observations, and standalone survey rendering |
| `combined_mapping.py` | Standalone combined map with directional and association-status layers |
| `analysis.py` | Report calculations and text presentation over already-loaded data |
| `mapping.py` | Standalone HTML presentation of already-qualified points |
| `dashboard.py` | Loopback HTTP routes, incremental dashboard snapshots, and enqueueing authorized control requests |
| `profiles/` | Packaged mobile/fixed TOML defaults, including the supplied Bluetooth addresses |
| `assets/` | Browser HTML/JavaScript/CSS, shared signal visualization, licensed Leaflet resources, and planning-area GeoJSON |

## Dependency direction

```mermaid
flowchart TD
    CLI[CLI] --> APP[Application supervisor]
    CLI --> SCANCLI[Scanner console workflow]
    SCANCLI --> SCAN[BLE discovery]
    SCANCLI --> SETTINGS[Settings and profiles]
    SCAN --> SETTINGS
    APP --> SETTINGS
    SETTINGS --> LOCK[OS locks]
    APP --> LOCK
    APP --> RADIO[BTECH acquisition]
    APP --> HTTP[HTTP dashboard]
    APP --> CONTROL[Lifecycle request channel]
    APP --> DB[SQLite storage]
    CLI --> DB
    CLI --> RULES
    CLI --> REPORT[Report renderer]
    CLI --> MAP[Static HTML renderer]
    HTTP --> DB
    HTTP --> RULES[Measurement rules]
    HTTP --> CONTROL
    REPORT --> RULES
    RADIO --> RULES
    DB --> RULES
```

The supervisor is a consumer of shared services. Settings, locks, discovery,
storage, and measurement rules must not import it. Discovery can be imported
without loading SQLite, the HTTP server, or optional Bluetooth backends. Optional
libraries are imported when the corresponding operation is actually requested.
Architecture tests check isolated imports and first-party dependency cycles.

## Lifecycle and resource ownership

`app.start` resolves personal settings, owns the database instance lock and the
writer's `Store`, and constructs the selected services. `run_together` keeps
Bluetooth polling and SQLite writes on the main asyncio thread. The HTTP server
runs in a managed thread and opens its own short-lived read connections.

The HTTP layer never mutates recording tables or drives the radio directly.
Authorized POST requests enter the `Controls` queue. The supervisor cancels and
awaits the collector before reset or exit. `Store.reset` owns backup verification
and the database transaction. It leaves settings and database file identity
intact. Exit acknowledges only a completion response actually sent to the browser.

`record` and the combined launcher share `benlink_radio.collect`; there is one polling
implementation. `benlink_radio.create_connection` constructs the connection for
any supported radio model; new Bluetooth sessions use source `benlink_bluetooth`.
Collection accepts a store and a connection factory rather than
creating a database or web server itself. The current composition uses Benlink;
another receiver adapter can be added without moving radio logic into the UI.

## Configuration and discovery

`profiles/mobile.toml` and `profiles/fixed.toml` are bootstrap defaults, not live
user configuration or web assets. Personal files are `mobile.toml` and
`fixed.toml`, are Git-ignored, and take precedence over defaults. Existing files
are not rewritten by loading. Relative data paths remain relative to the setup
file. `settings.profile_template` is the single template-resource lookup. The supported
recording roles are `mobile` and `fixed`; there is no repeater-role alias. A
fixed-site monitor is distinct from a future Kenwood receiver adapter.

`discovery.discover` returns candidate data and warnings. Advertised model names,
saved setup hints, and Bluetooth proximity measurements remain distinct from
verified hardware identity and RF-link RSSI. `scan_cli` decides how to present
that data and obtains an explicit selection. Only `settings.save_selection`
changes a setup file; it validates, backs up, and replaces it while preserving
unrelated fields and comments.

## Analysis and maps

`storage.load_log` owns read-only SQL and snapshot lifetime. Reports and HTTP
snapshots use it rather than maintaining separate database queries. CSV export
also lives in storage, with an explicitly closed connection.

`measurements` owns qualification rules shared by reports, static maps, and the
live dashboard. It compares GPS time with measurement time, retains original RSSI
units, and distinguishes receive direction. Storage input validation and GPS
qualification are separate: an observation can be worth recording without being
usable as a map point.

Offline combining uses `alignment.align_logs` over consistent input snapshots.
It keeps inferred coordinates out of recording tables and preserves input/session
provenance in a separate result bundle. Rendering receives results rather than
querying SQL or controlling radios.

`assets/visualization.js` owns the shared signal palette, percentile/bin
calculations, bounded timeline sampling, and SVG rendering. Live telemetry and
offline combined results adapt to this common measurement view; receiver
sessions remain separate. `combined-survey.js` owns combined-map filters and
linked selection, and `visualization.css` supplies shared presentation. Standalone
exports embed the helper assets, while the live server serves them locally.
The collector never depends on browser code.

Multi-transceiver acquisition uses `fleet.start_fleet`, which validates distinct
radio addresses, station names, resolved databases, and dashboard ports before
launching `start` in independent processes. Each child owns its existing
collector, database lock, and dashboard lifecycle. The supervisor signals every
child for graceful cleanup on Ctrl-C or an unexpected child failure; a dashboard
Exit stops only its own child. No second polling implementation is introduced.

`sharing.snapshot` uses SQLite backup to include committed WAL data without
requiring the recorder to stop. `sharing.merge_databases` creates a separate
database and preserves session UUIDs. Local sample/event integer IDs are remapped;
the `samples_origins` / `events_origins` tables retain each original session/row
identity. These tables participate in snapshot transactions and survive recursive
merging. Copies with the same identity and content deduplicate; conflicts abort
and remove the new output. Provenance foreign keys cascade on database reset.

`survey.create_survey` stages a complete bundle before publishing it to a newly
owned directory. Its lossless merged database retains all input sources and
diagnostics; HTML, CSV, and GIS follow source selection. `survey_data` adapts the
shared measurement rules to per-session receive observations, retaining fixed
history without inventing mobile coordinates. `gis` encodes only qualified
points. GeoPackage uses core SQLite tables and standard little-endian point WKB,
so no third-party GIS runtime is required. `survey.html` embeds local Leaflet and
shared visualization assets; `survey.js` owns linked transceiver/session/source/
UTC filters, map selection, and per-session comparisons. It performs no writes
to recording data or radio control. File sharing is explicit and offline; there
is no cross-computer live synchronization service.

Static HTML and the live browser application have different presentation
lifecycles. Their rendering remains separate, while measurement eligibility is
shared. The planning overlay is display context and does not discard out-of-area
observations. Offline `combine` locates fixed-site uplink records using mobile GPS; the
live fixed-site dashboard does not infer coordinates.

## Scripts and packaging

### Filename conventions

Python modules and maintenance scripts use `snake_case` names describing their
responsibility. `dashboard.py` owns the local HTTP dashboard, matching its
`dashboard.html` and `dashboard.js` assets and `test_dashboard.py` Python tests.
`benlink_ble.py` identifies the specific backend adaptation rather than implying
it implements general Bluetooth discovery. `build_planning_area.py` generates
the planning overlay; it does not build the application or collect measurements.

User launchers use hyphenated action names and matching `.cmd` / `.sh` stems
across platforms. `start-mobile` and `start-fixed` identify the station
roles they launch, independently of the selected radio model; `scan-radios` discovers and offers radio selection.
`run-gps-rssi` is the shared CLI runner used by those wrappers. The personal
`mobile.toml` and `fixed.toml` names match their packaged profile templates.
These role names are the sole defaults. Custom setup and database paths can
be supplied explicitly.

Root launchers:

- `start-mobile.cmd` / `start-mobile.sh`: launch the mobile setup.
- `start-fixed.cmd` / `start-fixed.sh`: launch the fixed setup.
- `scan-radios.cmd` / `scan-radios.sh`: scan and select.

They delegate virtual-environment lookup, checkout working directory, Python
invocation, and error handling to `scripts/run-gps-rssi.cmd` on Windows and
`scripts/run-gps-rssi.sh` on Linux. The Windows scanner also pauses after a
successful scan. Linux scripts use `exec` so process signals and exit status
reach the application and its caller. Both use the same Python CLI and profiles.

GitHub Actions runs Python and dashboard tests on Windows and Linux with Python
3.11 and 3.12, plus a launcher help smoke test on each OS. Linux launcher tests
exercise paths with spaces, argument forwarding, and failure propagation.
Hardware Bluetooth compatibility requires a separate physical-radio test; CI
uses simulated connections. See [platform setup](running.md).

`scripts/build_planning_area.py` is a maintenance tool, not a runtime dependency. Its GIS
packages are not imported by the application. Leaflet and planning data remain
packaged resources. Wheels must include `profiles/*.toml` and browser assets,
without old setup templates under `assets/`.

No scheduling, PTT automation, RF calibration, or Kenwood adapter is
introduced by this refactor. Those capabilities should be added at the relevant
acquisition, measurement, and composition boundaries rather than in launch scripts.
