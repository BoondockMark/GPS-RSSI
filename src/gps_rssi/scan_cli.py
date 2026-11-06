"""Interactive radio-selection presentation for the scan command."""

import asyncio
import json
from pathlib import Path

from .discovery import discover
from .settings import PROFILES, save_selection


def readable(value):
    return "".join(c if c.isprintable() else "?" for c in str(value))


def choose(prompt, options, input_fn):
    while True:
        answer = input_fn(prompt).strip().lower()
        if answer in ("", "q", "quit"):
            return None
        if answer in options:
            return options[answer]
        print("Choose one of the listed numbers, or q to cancel.")


def run_scan(args, *, input_fn=input):
    try:
        result = asyncio.run(discover(args.timeout, custom=args.config))
        if args.json:
            print(json.dumps(result, indent=2, allow_nan=False))
            return
        print(f"Found {len(result['candidates'])} radio candidates among {result['devices_seen']} Bluetooth devices.")
        print("Advertised names are not verified hardware models. BT signal strength is not RF-link RSSI.")
        for warning in result["warnings"]:
            print("Warning:", readable(warning))
        for number, radio in enumerate(result["candidates"], 1):
            signal = radio["bluetooth_rssi_dbm"]
            print(f"[{number}] {readable(radio['name']) or '(unnamed)'} | {readable(radio['address'])} | BT {signal if signal is not None else '?'} dBm")
            print("    Advertised model:", radio["advertised_model"] or "unknown (setup/service candidate)")
            for hint in radio["setup_hints"]:
                print("    Setup hint:", readable(hint))
        if not result["candidates"]:
            print("Enable radio Bluetooth, keep it nearby, close other control apps, and scan again.")
            return
        if not args.select:
            print("To save a candidate: gps-rssi scan --select (optionally --profile mobile or fixed).")
            return
        radio = choose("Select radio number (q cancels): ",
                       {str(i): r for i, r in enumerate(result["candidates"], 1)}, input_fn)
        if radio is None:
            print("Canceled; no settings changed.")
            return
        profile = args.profile
        if profile is None:
            print("[1] Mobile setup  [2] Fixed-site setup")
            profile = choose("Select setup (q cancels): ", {"1": "mobile", "2": "fixed"}, input_fn)
        if profile is None:
            print("Canceled; no settings changed.")
            return
        path = args.config or Path(PROFILES[profile])
        backup = save_selection(radio, path, profile=profile)
        print(f"Saved BLE address and advertised name to {path.resolve()}.")
        if backup:
            print(f"Previous settings backup: {backup}")
        print("Other settings and recording labels were preserved. This takes effect on the next launch.")
        print(f'Launch: gps-rssi start --profile {profile} --config "{path.resolve()}"')
    except (EOFError, KeyboardInterrupt):
        print("Canceled; no further settings changes requested.")
