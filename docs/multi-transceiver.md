# Multiple transceivers, sharing, and GIS

Each transceiver records independently, with its own station name, recording
sessions, database, and dashboard. This works across computers or on one computer
with an adapter that supports the required concurrent Bluetooth connections.
Data sharing uses portable files; no central server or Internet connection is
required. Live synchronization between computers is not implemented.

## Record on one or several computers

Copy `examples/mobile-a.toml` and `examples/mobile-b.toml` to personal setup files
in the checkout root. Replace `RADIO_A_ADDRESS` / `RADIO_B_ADDRESS` with each
radio's address. Set model, antenna, mounting, and channel notes in
`recording.label`. Give every physical transceiver a distinct `recording.station`.
Database paths are relative to the setup file, so copying the example files to
the checkout root puts their recordings under the root's `data/` directory.

For Bluetooth discovery and saving a selected address into each setup:

```sh
gps-rssi scan --select --profile mobile --config mobile-a.toml
gps-rssi scan --select --profile mobile --config mobile-b.toml
```

On one computer, launch both at once:

```sh
gps-rssi fleet --config mobile-a.toml --config mobile-b.toml
```

Add more `--config` arguments for additional transceivers, including a fixed-site
monitor's complete fixed profile. Addresses, station names, database files, and
dashboard ports must be distinct. These are checked before launching any
recorder. Each recorder uses the existing Bluetooth acquisition and app lifecycle;
one slow or disconnected radio does not block polling on another. Ordinary
Bluetooth failures retry within that recorder. An unexpected recorder process
failure stops the remaining fleet to avoid silently continuing an incomplete run.

One Ctrl-C requests graceful disconnect and shutdown of all recorders. After a
15-second cleanup grace period, an unresponsive process is terminated. Each
dashboard's **Exit app** stops only that transceiver; **Reset database** backs up
and resets only that transceiver's database. Closing a browser tab leaves its
recorder running. Use `--no-browser` for a headless fleet.

On separate computers, run one setup on each:

```sh
gps-rssi start --config mobile-a.toml
```

Keep the computer clocks synchronized before comparing time windows. Different
hardware models and antennas retain separate session statistics; their relative
RSSI scales are not calibrated to one another. Concurrent physical-radio
Bluetooth operation still needs validation with your adapters and radios.

## Share a complete recording safely

Create a portable snapshot, even while recording is running:

```sh
gps-rssi share --db data/mobile-a.sqlite --output data/mobile-a-share.sqlite
```

SQLite's backup API includes committed data from the live write-ahead log. The
snapshot is a standalone database with its sessions, samples, diagnostics, and
any merge provenance. Transfer this file through your preferred file-sharing
service or a USB drive. The command does not upload it or expose a network port.
Do not copy only the main `.sqlite` file of a running recorder: recent samples
may still be in its `-wal` file. Later samples stay in the running recorder;
create a new snapshot to share an update.

Merge shared files into a new, separate database:

```sh
gps-rssi merge --db data/mobile-a-share.sqlite --db data/mobile-b-share.sqlite --output data/team.sqlite
gps-rssi serve --db data/team.sqlite
```

Existing commands (`report`, `map`, `serve`, `export`, and selected-station
`combine`) can read the merged database. The dashboard remains a local viewer;
a merged snapshot does not update itself when the original radios keep recording.

Merging identifies readings by **original session UUID + original sample ID**,
not by the local integer sample ID, filename, timestamp, or station name. Two
radios can both have sample 1 without colliding. Repeated inputs, overlapping
snapshots, and previously merged files do not duplicate the same reading. An
updated snapshot adds new samples and events. Conflicting content under the same
identity fails the whole merge. Inputs and existing output files are never
overwritten. Events retain their own original IDs in the same way.

The merged database's sample/event integer IDs are local to that merged file;
`samples_origins` and `events_origins` retain the mapping back to original IDs.
Use the session UUID and original ID when comparing data between bundles. The
survey map and GIS exports expose the original sample identity explicitly.

## Build the multi-transceiver survey

```sh
gps-rssi survey --db data/mobile-a-share.sqlite --db data/mobile-b-share.sqlite --db data/fixed-share.sqlite --output data/team-survey
```

Supply any number of `--db` inputs; a fixed log is optional. The command merges
snapshots, removes duplicates, and creates a complete new directory. Open
`map.html` in a browser. No Python server is required for the exported map.

The view provides:

- Transceiver, recording-session, source, and inclusive UTC time filters.
- Receive counts and counts of qualified GPS points / observations without locations.
- Per-session median, 10th percentile, observed range, and mapped counts.
- A signal timeline and distribution for each receiver session, linked to map selection.
- Consistent signal fills, transceiver outline colors, and marked replay observations.
- GPS rejection reasons and a table of the most recent 500 filtered receive observations.

All filtered receive observations contribute to statistics; TX/idle samples do
not. Unsupported RSSI remains visible as unavailable rather than zero. Timeline
rendering keeps at most 602 actual observations per series while retaining weak
and strong extremes. Filters update the map, charts, counts, and tables together.
Filtering keeps the current map position; **Fit visible points** changes it on
demand. Select a comparison row to focus on its session. The layout supports
mobile screens, with wide charts and tables scrolling within their panels.

Map points require mobile downlink receive state, locked valid coordinates, GPS
time within 10 seconds of the sample, and reported GPS uncertainty no greater
than 100 meters. Adjust with `--max-gps-age` and `--max-gps-accuracy`. Zero RSSI and
closed-squelch receive points are retained. A point does not identify a
transmitter, prove usable voice reception, or rate unmeasured land.

Fixed receive history appears in charts and tables without a mobile location.
The survey does not choose one of several simultaneous mobile transmitters for
a fixed receive observation. For existing conservative two-station time
association, use `combine` with an explicit mobile station/session selection.

`map.html` embeds the measured data, Leaflet library, styles, and chart code, so
points, filters, tables, and charts work offline. Street tiles require Internet
access. Leaflet's license is included in the bundle. No tile prefetching occurs.

The folder contains:

| File | Contents / common application |
|---|---|
| `map.html` | Portable interactive survey |
| `measurements.gpkg` | GeoPackage 1.3 point layer for QGIS / ArcGIS |
| `measurements.geojson` | RFC 7946 point features for desktop/web GIS |
| `measurements.kml` | Point placemarks for Google Earth |
| `measurements.gpx` | GPX 1.1 waypoints with measurement extensions |
| `measurements.csv` | Every selected raw sample, including TX/idle and missing GPS |
| `summary.json` | Inputs, sessions, source, GPS limits, counts, and exclusions |
| `recordings.sqlite` | Lossless merged recordings for future analysis / re-merging |
| `Leaflet-LICENSE.txt` | Map-library license |

GIS files contain qualified mobile receive points only, in WGS84 (`EPSG:4326`),
using longitude/latitude coordinate order where the format requires it. They
retain original session/sample identity, source, UTC sample/fix times, relative
RSSI and its unit, squelch state, channel ID, receiver notes, and GPS uncertainty.
GeoPackage exposes common measurement columns and preserves all properties in
`properties_json`. GPX uses waypoints, with extra fields in its `rf` extension
namespace; applications that ignore extensions may show only names and locations.
No format invents tracks or fills gaps between observations.

The map, CSV, and GIS select live data by default. Use `--source replay` for
synthetic data, or `--source all` to include both. `recordings.sqlite` always
retains **all input sources and diagnostics**, including sessions excluded from
the map/CSV/GIS selection. View filters do not alter any export file. Every
bundle is a snapshot, so rebuild it into a new directory for newer data.

The files contain precise locations and recording metadata. Share the entire
folder for full GIS/data reuse, or just `map.html` for interactive viewing.

## Export an individual GIS file

```sh
gps-rssi export --db data/team.sqlite --output data/team.geojson
gps-rssi export --db data/team.sqlite --output data/team.gpkg
gps-rssi export --db data/team.sqlite --output data/team.kml
gps-rssi export --db data/team.sqlite --output data/team.gpx
```

Format is inferred from the output suffix. `--format` overrides it. Existing
CSV exports retain their default of all sources and all sample states. GIS
defaults to live mobile receive points with the same GPS rules as `survey`.
`--station`, `--session`, and `--source` select data for either output kind.
Output files must be new, and their parent directory must already exist.

## Validation

```sh
python -m unittest discover -s tests -v
node --test tests/*.test.cjs
```

Tests cover colliding local IDs, nested/overlapping snapshots, conflicting
identities, live WAL snapshots, failed-output cleanup, source and GPS selection,
all four GIS encodings, transceiver filters, bounded tables, linked selection,
fleet preflight, child failure, and graceful process cleanup. Physical radio
interoperability and RF calibration remain bench/field checks.
