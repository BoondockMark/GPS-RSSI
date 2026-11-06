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
    p = argparse.ArgumentParser(description="Read-only BTECH RF strength diagnostic logger")
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("record", "replay"):
        sub = commands.add_parser(name)
        sub.add_argument("--db", type=Path, required=True)
        sub.add_argument("--station", required=True)
        sub.add_argument("--role", choices=("mobile", "repeater"), default="mobile")
        if name == "replay":
            sub.add_argument("input", type=Path, help="JSONL frames; tagged as replay data")
        else:
            sub.add_argument("--address", required=True, help="Paired radio Bluetooth address/UUID")
            sub.add_argument("--transport", choices=("ble", "rfcomm"), default="ble")
            sub.add_argument("--rfcomm-channel", type=positive_int)
            sub.add_argument("--interval", type=positive, default=1)
            sub.add_argument("--timeout", type=positive, default=10)
            sub.add_argument("--reconnect-delay", type=positive, default=5)
            sub.add_argument("--gps", action="store_true", help="Request GPS only when radio reports a fix")
            sub.add_argument("--max-samples", type=positive_int)
            sub.add_argument("--label", default="", help="Radio model, firmware, antenna and power notes")
    export = commands.add_parser("export")
    export.add_argument("--db", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
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
