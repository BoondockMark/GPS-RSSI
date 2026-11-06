"""Read-only BLE discovery and candidate classification; no prompts or setup writes."""

import math
from pathlib import Path
import re

from .settings import setup_hints

SERVICE_UUID = "00001100-d102-11e1-9b23-00025b00a5a5"
MODEL_PATTERN = re.compile(r"(?<![A-Z0-9])(UV[- _]?50[- _]?PRO|UV[- _]?PRO|VR[- _]?N[- _]?7600|VR[- _]?N[- _]?76)(?![A-Z0-9])", re.I)
MODELS = {"UVPRO": "UV-PRO", "UV50PRO": "UV-50PRO", "VRN76": "VR-N76", "VRN7600": "VR-N7600"}


def model_hint(name):
    match = MODEL_PATTERN.search(name or "")
    return MODELS[re.sub(r"[^A-Z0-9]", "", match[1].upper())] if match else None


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
