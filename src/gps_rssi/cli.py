import argparse
import asyncio
import json
import logging
import math
import sqlite3
import sys
from pathlib import Path


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def finite_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be finite")
    return number


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def port_number(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return number


def parser():
    p = argparse.ArgumentParser(
        prog="gps-rssi",
        description="Log supported radios' RF signal strength and optional GPS locally.\n"
                    "Read-only: does not key the transmitter or change radio settings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Quick start (single-line commands work in Windows Command Prompt):
  gps-rssi scan
  gps-rssi scan --select
  gps-rssi start
  gps-rssi start --profile fixed
  gps-rssi fleet --config mobile-a.toml --config mobile-b.toml
  gps-rssi survey --db data/mobile-a.sqlite --db data/mobile-b.sqlite --output data/survey
  gps-rssi share --db data/mobile.sqlite --output data/mobile-share.sqlite
  gps-rssi combine --mobile-db data/mobile.sqlite --fixed-db data/fixed.sqlite --output data/combined
  gps-rssi export --db data/mobile.sqlite --output data/mobile.csv
  gps-rssi report --db data/mobile.sqlite
  gps-rssi map --db data/mobile.sqlite --output data/mobile-map.html
  gps-rssi serve --db data/mobile.sqlite

Command details:
  gps-rssi scan --help
  gps-rssi start --help
  gps-rssi record --help
  gps-rssi replay --help
  gps-rssi export --help
  gps-rssi fleet --help
  gps-rssi share --help
  gps-rssi merge --help
  gps-rssi survey --help
  gps-rssi report --help
  gps-rssi map --help
  gps-rssi serve --help

RSSI is a relative 0-100 value (16 levels), not dBm or Bluetooth signal strength.
Use combine for offline cross-station time alignment. Kenwood acquisition is not implemented yet.""")
    commands = p.add_subparsers(dest="command", required=True, title="commands")
    scan = commands.add_parser("scan", help="Find supported BLE radio candidates and optionally save a selection.",
        description="Discover UV-PRO, UV-50PRO, VR-N76, and VR-N7600 BLE candidates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  gps-rssi scan
  gps-rssi scan --select --profile fixed
  gps-rssi scan --json

Listing is read-only. --select prompts for a radio and, unless supplied, a mobile
or fixed setup. It saves only BLE connection fields, preserving other settings
and backing up an existing setup. No pairing, connection, or RF transmission is
performed; selections take effect next launch. Stop other radio control apps
before scanning. A radio must be advertising to appear.
Names are hints, not verified model IDs: a UV-50PRO may advertise VR-N7600.
Unnamed radios with the Benshi service or a known setup address are candidates
too. Listed BT dBm is Bluetooth proximity, not coverage RSSI.
On Windows, double-click scan-radios.cmd for the interactive workflow.""")
    scan.add_argument("--timeout", type=positive, default=10, metavar="SECONDS", help="Scan duration (default: 10 seconds)")
    output = scan.add_mutually_exclusive_group()
    output.add_argument("--select", action="store_true", help="Choose a candidate and save its BLE address/name to a setup")
    output.add_argument("--json", action="store_true", help="Print candidate metadata as JSON; no prompts or settings changes")
    scan.add_argument("--profile", choices=("mobile", "fixed"), help="Target setup defaults for selection; prompts if omitted")
    scan.add_argument("--config", type=Path, metavar="PATH", help="Custom setup file; default is chosen by profile")
    start = commands.add_parser("start", help="Start recording and dashboard using a saved mobile or fixed-site profile.",
        description="Start the radio logger and dashboard together using saved settings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Quick start:
  gps-rssi start
  gps-rssi start --no-browser
  gps-rssi start --profile fixed

On first launch, creates mobile.toml or fixed.toml for the selected station role.
Either role can use any supported radio. Bluetooth addresses live in [radio].address in
these files. Mobile defaults enable GPS and downlink logging; fixed defaults
disable GPS, log uplink RSSI, and use a separate database and dashboard port.
Existing settings are never overwritten; file values override profile defaults.
Database paths are relative to the settings file. Automatic RF transmissions
are not implemented. Use combine for offline cross-station time alignment.
One Ctrl-C stops both services and disconnects the radio. Closing the browser
does not stop recording. Stop older record/serve commands before using start.
The dashboard also has Exit app and Reset database buttons. Reset confirms the
active file, saves a timestamped backup, and resumes with a fresh session.
Use start-mobile.cmd/start-fixed.cmd on Windows or ./start-mobile.sh/./start-fixed.sh
on Linux; no activation needed.""")
    start.add_argument("--profile", choices=("mobile", "fixed"), default="mobile",
                       help="Initial setup defaults (default: mobile)")
    start.add_argument("--config", type=Path, metavar="PATH",
                       help="Personal TOML file; default: mobile.toml for mobile, fixed.toml for fixed; created if missing")
    start.add_argument("--no-browser", action="store_true", help="Run both services without opening a browser")
    fleet = commands.add_parser("fleet", help="Record multiple transceivers concurrently using separate setup files.",
        description="Launch one independent recorder and dashboard for each transceiver.",
        epilog="Repeat --config for each existing TOML setup. Use distinct radio addresses, station names, databases, "
               "and dashboard ports. One Ctrl-C stops all recorders. A recorder failure stops the fleet; "
               "Exit in one dashboard stops only that transceiver. Use survey to visualize their shared data.")
    fleet.add_argument("--config", action="append", type=Path, required=True, help="Existing transceiver TOML setup; repeat for each radio")
    fleet.add_argument("--no-browser", action="store_true", help="Do not open dashboard browser tabs")
    share = commands.add_parser("share", help="Create a portable SQLite snapshot, including data in the live WAL.",
        epilog="Safe while recording. Transfer the new file using your preferred file-sharing service; no upload is performed.")
    share.add_argument("--db", type=Path, required=True, help="Existing recording database; opened read-only")
    share.add_argument("--output", type=Path, required=True, help="New standalone .sqlite file; never overwritten")
    merge = commands.add_parser("merge", help="Merge multiple recording databases without duplicating overlapping copies.",
        epilog="Session UUIDs and original reading IDs are preserved. Conflicting copies fail without creating output. "
               "Input databases are read-only. The result can be shared, merged again, or opened with serve/report.")
    merge.add_argument("--db", type=Path, action="append", required=True, help="Input SQLite database; repeat for each input")
    merge.add_argument("--output", type=Path, required=True, help="New merged .sqlite file; never overwritten")
    survey = commands.add_parser("survey", help="Build a portable multi-transceiver map, comparison charts, and GIS bundle.",
        epilog="Repeat --db for mobile and optional fixed recordings. Creates map.html, recordings.sqlite, measurements.csv, "
               "GeoJSON, KML, GPX, GeoPackage, and summary.json. GIS contains qualified mobile receive points only; "
               "fixed receive history is shown separately without inferred mobile locations. No RF transmission or upload.")
    survey.add_argument("--db", type=Path, action="append", required=True, help="Input SQLite database; repeat for each transceiver")
    survey.add_argument("--output", type=Path, required=True, help="New survey directory; must not exist")
    survey.add_argument("--source", choices=("live", "replay", "all"), default="live", help="Map/CSV/GIS source (default: live); recordings.sqlite retains all inputs")
    survey.add_argument("--max-gps-age", type=positive, default=10, help="Maximum absolute sample/fix time difference in seconds (default: 10)")
    survey.add_argument("--max-gps-accuracy", type=positive, default=100, help="Maximum reported GPS uncertainty in meters (default: 100)")
    descriptions = {
        "record": "Collect live RSSI, radio state, and optional GPS over Bluetooth.",
        "replay": "Import JSONL frames into SQLite as a new, explicitly tagged replay session.",
    }
    examples = {
        "record": """Advanced examples (replace RADIO_ADDRESS with [radio].address from your setup file):
  gps-rssi record --address RADIO_ADDRESS --station mobile --db data/mobile.sqlite --max-samples 30
  gps-rssi record --address RADIO_ADDRESS --station fixed --role fixed --db data/fixed.sqlite --max-samples 30

For saved settings without repeating addresses, use gps-rssi start or
gps-rssi start --profile fixed instead.

Before recording:
  Enable Bluetooth on the radio and pair it with this computer. Close other
  radio-control apps. For initial tests, disable scanning/dual watch manually.
  BLE is the default. RFCOMM needs the command service's discovered channel;
  do not use a KISS/TNC or audio service or assume channel 1.

During recording:
  Omit --max-samples to run until Ctrl-C. Saved samples survive interruption.
  Disconnected radios are retried until stopped; --max-samples counts successful
  samples, not attempts. Every run appends a new session to the database.
  BLE discovery and connection get 30 seconds; --connect-timeout changes this.
  Failures identify connect, device_info, status, or position. Cleanup failures
  are reported separately. Close phone apps and other loggers if the link is busy.
  --gps queries position only when the radio reports GPS lock. Try it outdoors.
  GPS=True in the log means a position was returned; check its age and accuracy.
  GPS failure keeps the RSSI sample without coordinates and reconnects.
  RSSI is relative (16 levels scaled to 0-100), not calibrated dBm.""",
        "replay": """Example:
  gps-rssi replay examples/mobile.jsonl --db data/demo.sqlite --station demo-mobile

Input format:
  One JSON object per line, with a status object and an optional position object.
  See examples/mobile.jsonl for the required fields and synthetic sample data.
  Blank lines are ignored. Malformed input reports its line number and stops;
  earlier samples remain saved. Each run appends a new session tagged replay.
  Replay imports immediately, without Bluetooth or original timing delays.""",
    }
    for name in ("record", "replay"):
        sub = commands.add_parser(name, help=descriptions[name], description=descriptions[name],
                                  epilog=examples[name], formatter_class=argparse.RawDescriptionHelpFormatter)
        sub.add_argument("--db", type=Path, required=True, metavar="PATH",
                         help="SQLite log file; creates parent directories and appends a new session")
        sub.add_argument("--station", required=True, metavar="NAME",
                         help="Station identifier saved with this session, e.g. mobile")
        sub.add_argument("--role", choices=("mobile", "fixed"), default="mobile",
                         help="Station role: mobile=downlink; fixed=uplink (default: mobile)")
        if name == "replay":
            sub.add_argument("input", type=Path, help="JSONL frame file; imported samples are tagged as replay data")
        else:
            sub.add_argument("--address", required=True, metavar="ADDRESS",
                             help="Paired radio address/UUID; find it in [radio].address in your personal setup file")
            sub.add_argument("--transport", choices=("ble", "rfcomm"), default="ble",
                             help="Bluetooth Low Energy or Bluetooth Classic serial (default: ble)")
            sub.add_argument("--rfcomm-channel", type=positive_int, metavar="N",
                             help="Positive command-service channel number; required with --transport rfcomm")
            sub.add_argument("--interval", type=positive, default=1, metavar="SECONDS",
                             help="Target time between sample starts; requests may take longer (default: 1 second)")
            sub.add_argument("--timeout", type=positive, default=10, metavar="SECONDS",
                             help="Time limit for each radio command and disconnect cleanup (default: 10 seconds)")
            sub.add_argument("--connect-timeout", type=positive, default=30, metavar="SECONDS",
                             help="Time limit for Bluetooth discovery and connection setup (default: 30 seconds)")
            sub.add_argument("--reconnect-delay", type=positive, default=5, metavar="SECONDS",
                             help="Wait before reconnecting after a failure (default: 5 seconds)")
            sub.add_argument("--gps", action="store_true",
                             help="Request radio GPS position when locked; disabled by default")
            sub.add_argument("--max-samples", type=positive_int, metavar="N",
                             help="Stop after N saved status samples; default: run until Ctrl-C")
            sub.add_argument("--label", default="", metavar="TEXT",
                             help='Session notes, e.g. --label "UV-PRO; roof antenna; low power"')
    export = commands.add_parser(
        "export", help="Export CSV or qualified mobile points to GeoJSON, KML, GPX, or GeoPackage.",
        description="Export recording samples as CSV or GIS points in WGS84.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  gps-rssi export --db data/mobile.sqlite --output data/mobile.csv
  gps-rssi export --db data/mobile.sqlite --output data/mobile.gpkg
  gps-rssi export --db data/mobile.sqlite --output data/mobile.geojson

CSV includes all samples by default: station, role, source, timestamps, relative
RSSI, radio state, GPS columns, and decoded payload JSON. Missing fixes have empty
coordinate columns. GIS defaults to live data and includes only mobile receive
points with qualified GPS; TX/idle, missing/stale/inaccurate fixes, and fixed-site
observations are excluded. --source, --station, and --session narrow the selection.
GeoJSON, KML, GPX waypoints, and GeoPackage use WGS84 longitude/latitude and retain
station/session identity, original sample ID, RSSI unit, GPS accuracy, and notes.
Use survey for a multi-transceiver map and all export formats in one bundle.
The database is opened read-only. An existing output file is never overwritten;
choose a new filename for each export. The output directory must already exist.
This command exports samples, not the separate connection diagnostics table.""")
    export.add_argument("--db", type=Path, required=True, metavar="PATH",
                        help="Existing SQLite log file to read")
    export.add_argument("--output", type=Path, required=True, metavar="PATH",
                        help="New file to create; format inferred from suffix unless --format is supplied")
    export.add_argument("--format", choices=("csv", "geojson", "kml", "gpx", "gpkg"), help="Output format; defaults to filename suffix, otherwise CSV")
    export.add_argument("--station", help="Select one exact station name")
    export.add_argument("--session", help="Select one session UUID")
    export.add_argument("--source", choices=("live", "replay", "all"), help="Source selection (default: all for CSV, live for GIS)")
    export.add_argument("--max-gps-age", type=positive, default=10, help="GIS maximum sample/fix time difference in seconds (default: 10)")
    export.add_argument("--max-gps-accuracy", type=positive, default=100, help="GIS maximum reported GPS uncertainty in meters (default: 100)")
    for name, description in (
        ("report", "Summarize samples, receive RSSI, GPS quality, and connection errors."),
        ("map", "Create an HTML map of mobile downlink receive measurements."),
        ("serve", "Show a local live dashboard following the SQLite log."),
    ):
        sub = commands.add_parser(name, help=description, description=description,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
        sub.add_argument("--db", type=Path, required=True, metavar="PATH", help="Existing SQLite log; opened read-only")
        sub.add_argument("--station", metavar="NAME", help="Select one exact station name; default: all stations")
        sub.add_argument("--session", metavar="ID", help="Select one full session ID from report; default: all sessions")
        default_source = "all" if name == "report" else "live"
        sub.add_argument("--source", choices=("live", "replay", "all"), default=default_source,
                         help=f"Select live or synthetic replay sessions (default: {default_source})")
        sub.add_argument("--max-gps-age", type=positive, default=10, metavar="SECONDS",
                         help="Maximum absolute difference between GPS and sample time (default: 10 seconds)")
        sub.add_argument("--max-gps-accuracy", type=positive, default=100, metavar="METERS",
                         help="Maximum radio-reported GPS accuracy radius (default: 100 meters); missing accuracy is excluded")
        if name == "report":
            sub.add_argument("--json", action="store_true", help="Print structured JSON instead of the text report")
            sub.epilog = """Examples:
  gps-rssi report --db data/mobile.sqlite
  gps-rssi report --db data/mobile.sqlite --station mobile --json

Reports include sessions with zero samples and errors by operation. Radio-state
counts overlap; GPS and connection errors can describe the same failure.
GPS age is checked at measurement time, not against today's date.
RSSI statistics include receive samples only and remain in their original units.
Use station/session filters to assess different radios or configurations separately."""
        elif name == "serve":
            sub.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1",
                             help="Local bind address (default: 127.0.0.1)")
            sub.add_argument("--port", type=port_number, default=8765, help="Local HTTP port (default: 8765)")
            sub.add_argument("--refresh", type=positive, default=2, metavar="SECONDS",
                             help="Time between completed dashboard polls (default: 2 seconds)")
            sub.add_argument("--max-points", type=positive_int, default=5000, metavar="N",
                             help="Retained map point limit and initial sample window (default: 5000)")
            sub.add_argument("--area", choices=("la-habra-heights", "none"), default="la-habra-heights",
                             help="Planning overlay (default: La Habra Heights boundary + 0.5-mile buffer)")
            sub.epilog = """Example (run in a second terminal while record is running):
  gps-rssi serve --db data/mobile.sqlite

Start the logger first so the database exists. Open the printed address in a
browser on this computer. Ctrl-C stops the dashboard without stopping the logger.
The dashboard reads SQLite only; it does not connect to or control the radio.
New samples are fetched incrementally. Pause updates and follow-newest controls
do not affect recording. Old points remain in SQLite when trimmed from the map.
The planning buffer extends 0.5 mile (804.672 m) from the generalized Census 2022
city polygon. Points outside it are retained. This is not a legal boundary.
Leaflet is bundled locally; OpenStreetMap street tiles require Internet access.
Live data is selected by default; --source all includes labeled replay points.
This local-only dashboard does not expose your log to other computers."""
        else:
            sub.add_argument("--output", type=Path, required=True, metavar="PATH",
                             help="New HTML file; existing files are never overwritten; parent directory must exist")
            sub.epilog = """Examples:
  gps-rssi map --db data/mobile.sqlite --output data/mobile-map.html
  gps-rssi map --db data/demo.sqlite --source replay --output data/demo-map.html

Open the HTML file in your browser. Interactive maps require Internet access for
Leaflet and OpenStreetMap tiles; embedded measurements remain available in a table.
Only mobile downlink receive samples with usable GPS are plotted. TX/idle,
missing/unlocked/invalid fixes, excessive time offsets, and poor/unknown accuracy
are excluded and counted. Zero RSSI and closed-squelch receive samples are kept.
Live data is selected by default. --source all includes distinctly marked replay
points. Layers are grouped by station/session; points do not imply coverage
between them, identify the transmitter, or establish usable two-way voice."""
    combine = commands.add_parser("combine", help="Align two station logs and export a directional survey map and CSV.",
        description="Associate receive RSSI with mobile GPS and opposite-station TX observations, reading both logs only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Example (single line on Windows or Linux):
  gps-rssi combine --mobile-db data/mobile.sqlite --fixed-db data/fixed.sqlite --output data/combined

Creates a NEW directory containing measurements.csv, map.html and summary.json.
Select a station/session explicitly if a database contains multiple stations.
Associated points appear by default; optional unconfirmed layers retain location-only matches.
No interpolation, automatic clock correction, transmission, or input-database changes.
Clock corrections are ADDED to host timestamps: a clock 2 seconds fast needs -2.
Keep computers synchronized to UTC. Missing request timestamps cannot support matching.
RSSI scales are receiver-specific. Time association does not prove transmitter identity.
The HTML map uses Leaflet from unpkg.com and OpenStreetMap tiles; its table works offline.""")
    for role in ("mobile", "fixed"):
        combine.add_argument(f"--{role}-db", type=Path, required=True, help=f"Existing {role} SQLite log; read-only")
        combine.add_argument(f"--{role}-station", help=f"Exact {role} station name")
        combine.add_argument(f"--{role}-session", help=f"Exact {role} session ID")
        combine.add_argument(f"--{role}-clock-offset", dest=role + "_offset", type=finite_number, default=0,
                             metavar="SECONDS", help="Seconds added to this host's clock (default: 0); GPS time unchanged")
    combine.add_argument("--output", type=Path, required=True, help="New output directory; must not already exist")
    combine.add_argument("--source", choices=("live", "replay"), default="live", help="Input source (default: live)")
    for flag, default, help_text in (
        ("max-gps-gap", 2, "Maximum receive/GPS-fix time separation in seconds"),
        ("max-gps-age", 10, "Maximum GPS-fix age relative to its own observation in seconds"),
        ("max-gps-accuracy", 50, "Maximum reported GPS uncertainty in meters"),
        ("max-gap", 3, "Maximum connection continuity gap and full TX-bracket duration in seconds"),
        ("max-request", 1, "Maximum status request/reply duration in seconds"),
        ("transition-guard", .25, "Required margin around the RX interval for bracketing TX observations in seconds"),
    ):
        combine.add_argument("--" + flag, type=positive, default=default, help=f"{help_text} (default: {default})")
    return p


def run(args):
    if args.command == "fleet":
        from .fleet import start_fleet
        start_fleet(args.config, no_browser=args.no_browser)
        return
    if args.command == "share":
        from .sharing import snapshot
        print(f"Wrote portable snapshot: {snapshot(args.db, args.output)}")
        return
    if args.command == "merge":
        from .sharing import merge_databases
        counts = merge_databases(args.db, args.output)
        print(f"Wrote {args.output}: {counts['sessions']} sessions, {counts['samples']} samples, "
              f"{counts['events']} events; skipped {counts['duplicate_samples']} duplicate samples")
        return
    if args.command == "survey":
        from .survey import create_survey
        summary = create_survey(args.db, args.output, source=args.source,
                                max_age=args.max_gps_age, max_accuracy=args.max_gps_accuracy)
        print(f"Wrote {args.output}: {summary['station_count']} station names, "
              f"{summary['receive_observations']} receive observations, {summary['mapped_points']} GIS points")
        print("Open map.html to explore; GeoJSON, KML, GPX, GeoPackage, CSV, and recordings.sqlite are included.")
        return
    if args.command == "combine":
        from .alignment import DEFAULTS
        from .combine import combine
        summary = combine(args.mobile_db, args.fixed_db, args.output,
            mobile_station=args.mobile_station, fixed_station=args.fixed_station,
            mobile_session=args.mobile_session, fixed_session=args.fixed_session, source=args.source,
            **{key: getattr(args, key) for key in DEFAULTS})
        print(f"Wrote {args.output}: measurements.csv, map.html, summary.json")
        for status in ("associated", "unconfirmed", "unmatched"):
            print(f"  {status}: {summary['counts'].get(status, 0)}")
        for reason, count in sorted(summary["reasons"].items()):
            print(f"  {reason}: {count}")
        return
    if args.command == "scan":
        from .scan_cli import run_scan
        run_scan(args)
        return
    if args.command == "start":
        from .app import start
        start(args.config, profile=args.profile, no_browser=args.no_browser)
        return
    if args.command == "serve":
        from .dashboard import serve
        serve(args.db, host=args.host, port=args.port, refresh=args.refresh, area=args.area,
              station=args.station, session=args.session, source=args.source,
              max_age=args.max_gps_age, max_accuracy=args.max_gps_accuracy,
              max_points=args.max_points)
        return
    if args.command in ("report", "map"):
        from .analysis import format_report, make_report
        from .storage import load_log
        sessions, samples, events = load_log(args.db, station=args.station,
                                             session=args.session, source=args.source)
        limits = {"max_age": args.max_gps_age, "max_accuracy": args.max_gps_accuracy}
        if args.command == "report":
            report = make_report(sessions, samples, events, **limits)
            print(json.dumps(report, indent=2, allow_nan=False) if args.json else format_report(report))
        else:
            from .mapping import render_map
            from .measurements import select_points
            points, excluded = select_points(sessions, samples, **limits)
            page = render_map(points, excluded, **limits)
            with args.output.open("x", encoding="utf-8") as output:
                output.write(page)
            print(f"Wrote {args.output}: {len(points)} plotted, {sum(excluded.values())} excluded "
                  f"from {len(samples)} selected samples (source={args.source}).")
            for reason, count in sorted(excluded.items()):
                print(f"  {reason}: {count}")
            if not points:
                print("No eligible points. Check report, source selection, GPS times, and accuracy.")
        return
    if args.command == "export":
        from .storage import export_csv
        suffix = args.output.suffix.lower().lstrip(".")
        format = args.format or (suffix if suffix in ("csv", "geojson", "kml", "gpx", "gpkg") else "csv")
        source = args.source or ("all" if format == "csv" else "live")
        if format == "csv":
            export_csv(args.db, args.output, station=args.station, session=args.session, source=source)
        else:
            from .gis import export_points
            from .survey import survey_data
            points, _, summary = survey_data(args.db, station=args.station, session=args.session, source=source,
                                             max_age=args.max_gps_age, max_accuracy=args.max_gps_accuracy)
            export_points(points, args.output, format, metadata=summary)
            print(f"Wrote {args.output}: {len(points)} qualified mobile receive points (EPSG:4326)")
        return
    if args.command == "record":
        from .benlink_radio import collect, create_connection
        if args.transport == "rfcomm" and args.rfcomm_channel is None:
            raise ValueError("RFCOMM requires --rfcomm-channel from service discovery; do not assume channel 1")
        # Fail before opening a session if the optional dependency is absent.
        factory = lambda: create_connection(args.address, args.transport, args.rfcomm_channel,
                                     connect_timeout=args.connect_timeout)
        factory()
    from .storage import Store
    metadata = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    store = Store(args.db, args.station, args.role,
                  "replay" if args.command == "replay" else "benlink_bluetooth", metadata)
    try:
        if args.command == "replay":
            with args.input.open(encoding="utf-8") as source:
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    try:
                        store.sample(json.loads(line))
                    except (ValueError, KeyError, TypeError) as exc:
                        raise ValueError(f"{args.input}:{line_number}: {exc}") from exc
        else:
            asyncio.run(collect(store, factory, interval=args.interval, timeout=args.timeout,
                                gps=args.gps, reconnect_delay=args.reconnect_delay,
                                max_samples=args.max_samples, connect_timeout=args.connect_timeout))
    except KeyboardInterrupt:
        store.event("stopped", {"reason": "keyboard_interrupt"})
    except Exception as exc:
        store.event("error", {"error": str(exc), "type": type(exc).__name__})
        raise
    finally:
        store.close()


def main():
    # Fleet children on Windows receive Ctrl-Break in their own process group.
    import signal
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(parser().parse_args())
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"gps-rssi: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
