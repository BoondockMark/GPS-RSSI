import argparse
import asyncio
import csv
import json
import logging
import math
import sqlite3
import sys
from pathlib import Path

from .storage import Store


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
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
        description="Log BTECH radio RF signal strength and optional GPS locally.\n"
                    "Read-only: does not key the transmitter or change radio settings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Quick start (single-line commands work in Windows Command Prompt):
  gps-rssi record --address 38:D2:00:00:F3:34 --station mobile-uvpro --db data/uvpro.sqlite --max-samples 30
  gps-rssi record --address 38:D2:00:00:F3:34 --station mobile-uvpro --db data/uvpro-gps.sqlite --gps --max-samples 30
  gps-rssi export --db data/uvpro-gps.sqlite --output data/uvpro-gps.csv
  gps-rssi report --db data/uvpro-gps.sqlite
  gps-rssi map --db data/uvpro-gps.sqlite --output data/uvpro-map.html
  gps-rssi serve --db data/uvpro-gps.sqlite

Command details:
  gps-rssi record --help
  gps-rssi replay --help
  gps-rssi export --help
  gps-rssi report --help
  gps-rssi map --help
  gps-rssi serve --help

RSSI is a relative 0-100 value (16 levels), not dBm or Bluetooth signal strength.
Kenwood acquisition and cross-station pairing are not implemented yet.""")
    commands = p.add_subparsers(dest="command", required=True, title="commands")
    descriptions = {
        "record": "Collect live RSSI, radio state, and optional GPS over Bluetooth.",
        "replay": "Import JSONL frames into SQLite as a new, explicitly tagged replay session.",
    }
    examples = {
        "record": """Examples (your UV-PRO address; each command is one line):
  gps-rssi record --address 38:D2:00:00:F3:34 --station mobile-uvpro --db data/uvpro.sqlite --max-samples 30
  gps-rssi record --address 38:D2:00:00:F3:34 --station mobile-uvpro --db data/uvpro-gps.sqlite --gps --max-samples 30

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
                         help="Station identifier saved with this session, e.g. mobile-uvpro")
        sub.add_argument("--role", choices=("mobile", "repeater"), default="mobile",
                         help="Receive-direction label: mobile=downlink, repeater=uplink (default: mobile). "
                              "Repeater means a BTECH site monitor, not a Kenwood interface")
        if name == "replay":
            sub.add_argument("input", type=Path, help="JSONL frame file; imported samples are tagged as replay data")
        else:
            sub.add_argument("--address", required=True, metavar="ADDRESS",
                             help="Paired radio Bluetooth address/UUID; your UV-PRO: 38:D2:00:00:F3:34")
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
        "export", help="Export all recorded and replayed samples to a new CSV file.",
        description="Export all sessions and samples in a SQLite log to CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Example:
  gps-rssi export --db data/uvpro-gps.sqlite --output data/uvpro-gps.csv

Includes station, role, source (live or replay), timestamps, relative RSSI,
radio state, GPS columns, and decoded payload JSON. Missing fixes have empty
coordinate columns. GPS accuracy is retained in the payload JSON.
The database is opened read-only. An existing output file is never overwritten;
choose a new filename for each export. The output directory must already exist.
This command exports samples, not the separate connection diagnostics table.""")
    export.add_argument("--db", type=Path, required=True, metavar="PATH",
                        help="Existing SQLite log file to read")
    export.add_argument("--output", type=Path, required=True, metavar="PATH",
                        help="New CSV file to create; must not already exist")
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
  gps-rssi report --db data/uvpro-gps.sqlite
  gps-rssi report --db data/uvpro-gps.sqlite --station mobile-uvpro --json

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
  gps-rssi serve --db data/uvpro-gps.sqlite

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
  gps-rssi map --db data/uvpro-gps.sqlite --output data/uvpro-map.html
  gps-rssi map --db data/demo.sqlite --source replay --output data/demo-map.html

Open the HTML file in your browser. Interactive maps require Internet access for
Leaflet and OpenStreetMap tiles; embedded measurements remain available in a table.
Only mobile downlink receive samples with usable GPS are plotted. TX/idle,
missing/unlocked/invalid fixes, excessive time offsets, and poor/unknown accuracy
are excluded and counted. Zero RSSI and closed-squelch receive samples are kept.
Live data is selected by default. --source all includes distinctly marked replay
points. Layers are grouped by station/session; points do not imply coverage
between them, identify the transmitter, or establish usable two-way voice."""
    return p


def run(args):
    if args.command == "serve":
        from .live import serve
        serve(args.db, host=args.host, port=args.port, refresh=args.refresh, area=args.area,
              station=args.station, session=args.session, source=args.source,
              max_age=args.max_gps_age, max_accuracy=args.max_gps_accuracy,
              max_points=args.max_points)
        return
    if args.command in ("report", "map"):
        from .analysis import format_report, load_log, make_report
        sessions, samples, events = load_log(args.db, station=args.station,
                                             session=args.session, source=args.source)
        limits = {"max_age": args.max_gps_age, "max_accuracy": args.max_gps_accuracy}
        if args.command == "report":
            report = make_report(sessions, samples, events, **limits)
            print(json.dumps(report, indent=2, allow_nan=False) if args.json else format_report(report))
        else:
            from .mapping import render_map, select_points
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
        # Open read-only: a misspelled path must not create an empty database.
        with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("""SELECT s.station, s.role, s.source, m.*
                FROM samples m JOIN sessions s ON s.id=m.session_id ORDER BY m.id""")
            with args.output.open("x", newline="", encoding="utf-8") as output:
                writer = csv.writer(output)
                writer.writerow([column[0] for column in rows.description])
                writer.writerows(rows)
        return
    if args.command == "record":
        from .btech import collect, connection
        if args.transport == "rfcomm" and args.rfcomm_channel is None:
            raise ValueError("RFCOMM requires --rfcomm-channel from service discovery; do not assume channel 1")
        # Fail before opening a session if the optional dependency is absent.
        factory = lambda: connection(args.address, args.transport, args.rfcomm_channel,
                                     connect_timeout=args.connect_timeout)
        factory()
    metadata = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    store = Store(args.db, args.station, args.role,
                  "replay" if args.command == "replay" else "btech_bluetooth", metadata)
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(parser().parse_args())
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"gps-rssi: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
