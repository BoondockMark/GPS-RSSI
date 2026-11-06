"""BLE candidate discovery and explicit selection for personal setup files."""

import asyncio
from datetime import datetime, timezone
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import re
import tempfile
import tomllib
import uuid

from .app import InstanceLock, PROFILES, load_settings

SERVICE_UUID = "00001100-d102-11e1-9b23-00025b00a5a5"
MODEL_PATTERN = re.compile(r"(?<![A-Z0-9])(UV[- _]?50[- _]?PRO|UV[- _]?PRO|VR[- _]?N[- _]?7600|VR[- _]?N[- _]?76)(?![A-Z0-9])", re.I)
MODELS = {"UVPRO": "UV-PRO", "UV50PRO": "UV-50PRO", "VRN76": "VR-N76", "VRN7600": "VR-N7600"}


def model_hint(name):
    match = MODEL_PATTERN.search(name or "")
    return MODELS[re.sub(r"[^A-Z0-9]", "", match[1].upper())] if match else None


def setup_hints(directory, custom=None):
    """Read hints without creating personal files or modifying configuration."""
    paths = [(profile, directory / name, name) for profile, name in PROFILES.items()]
    if custom is not None and all(path.resolve() != custom.resolve() for _, path, _ in paths):
        paths.append(("custom", custom, None))
    hints, warnings = {}, []
    for profile, path, template in paths:
        try:
            personal = path.exists()
            defaults = tomllib.loads(files("gps_rssi").joinpath("assets/" + template).read_text(encoding="utf-8")) if template else {}
            if personal:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            elif template:
                data = defaults
            else:
                continue
            address = data.get("radio", {}).get("address", defaults.get("radio", {}).get("address", ""))
            label = data.get("recording", {}).get("label", defaults.get("recording", {}).get("label", profile))
            if isinstance(address, str) and address:
                hints.setdefault(address.casefold(), []).append(
                    f"{profile}: {label} ({'personal setup' if personal else 'setup template'})")
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            warnings.append(f"Could not read setup hint from {path}: {exc}")
    return hints, warnings


def candidates_from_advertisements(advertisements, hints):
    candidates = {}
    for device, advert in advertisements.values():
        address = device.address
        name = advert.local_name or device.name or ""
        model = model_hint(name)
        matched = hints.get(address.casefold(), [])
        service = SERVICE_UUID in {s.lower() for s in (advert.service_uuids or [])}
        if not (model or matched or service):
            continue
        signal = advert.rssi
        if type(signal) not in (int, float) or not math.isfinite(signal):
            signal = None
        candidate = {"address": address, "name": name, "advertised_model": model,
                     "setup_hints": matched, "compatible_service": service,
                     "bluetooth_rssi_dbm": signal}
        previous = candidates.get(address.casefold())
        if previous is None or (not previous["name"] and name):
            candidates[address.casefold()] = candidate
    return sorted(candidates.values(), key=lambda r: (r["name"].casefold(), r["address"].casefold()))


async def discover(timeout=10, *, directory=None, custom=None, scanner=None):
    if scanner is None:
        try:
            from bleak import BleakScanner
        except ImportError as exc:
            raise RuntimeError('Install Bluetooth support: python -m pip install -e ".[bluetooth]"') from exc
        scanner = BleakScanner
    hints, warnings = setup_hints(directory or Path.cwd(), custom)
    try:
        advertisements = await scanner.discover(timeout=timeout, return_adv=True)
    except Exception as exc:
        raise RuntimeError(f"Bluetooth scan failed ({type(exc).__name__}): {exc}. "
                           "Check Bluetooth is enabled and the radio is advertising.") from exc
    return {"devices_seen": len(advertisements),
            "candidates": candidates_from_advertisements(advertisements, hints), "warnings": warnings}


def save_selection(candidate, path, *, profile):
    """Update only BLE connection fields, with backup and atomic replacement."""
    try:
        import tomlkit
    except ImportError as exc:
        raise RuntimeError('Setup editing requires: python -m pip install -e ".[bluetooth]"') from exc
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with InstanceLock(path.with_name(path.name + ".app.lock"),
                      message="Another setup edit is running; retry the selection"):
        original = path.read_bytes() if path.exists() else None
        template = files("gps_rssi").joinpath("assets/" + PROFILES[profile]).read_bytes()
        doc = tomlkit.parse((original if original is not None else template).decode("utf-8"))
        if "radio" not in doc:
            doc["radio"] = tomlkit.table()
        doc["radio"]["address"] = candidate["address"]
        doc["radio"]["transport"] = "ble"
        doc["radio"]["advertised_name"] = candidate["name"]
        content = tomlkit.dumps(doc).encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", suffix=".toml", dir=path.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            load_settings(temporary, profile=profile)  # Validate without touching the target.
            current = path.read_bytes() if path.exists() else None
            if current != original:
                raise RuntimeError("Setup changed during selection; retry without overwriting it")
            if original is not None:
                directory = path.parent / "setup-backups"
                directory.mkdir(exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = directory / f"{path.name}-{stamp}-{uuid.uuid4().hex[:8]}.bak"
                with backup.open("xb") as output:
                    output.write(original)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return backup


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
