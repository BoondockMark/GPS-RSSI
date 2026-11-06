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

Command details:
  gps-rssi record --help
  gps-rssi replay --help
  gps-rssi export --help

RSSI is a relative 0-100 value (16 levels), not dBm or Bluetooth signal strength.
Kenwood acquisition, cross-station pairing, and mapping are not implemented yet.""")
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
                             help="Time limit for each connection/command operation (default: 10 seconds)")
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
    return p


def run(args):
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
        factory = lambda: connection(args.address, args.transport, args.rfcomm_channel)
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
                                max_samples=args.max_samples))
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
