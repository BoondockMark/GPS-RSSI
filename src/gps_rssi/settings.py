"""Profiles, personal settings, and validated setup-file updates.

This module owns all setup-file I/O and never starts application services.
"""

from datetime import datetime, timezone
from importlib.resources import files
import logging
import math
import os
from pathlib import Path
import tempfile
import tomllib
import uuid

from .locking import InstanceLock

LOG = logging.getLogger(__name__)
PROFILES = {"mobile": "mobile.toml", "fixed": "fixed.toml"}
LEGACY_PROFILES = {"mobile": "uvpro.toml", "fixed": "uv50pro.toml"}


def profile_template(profile):
    """Return the single packaged source of defaults for a named profile."""
    if profile not in PROFILES:
        raise ValueError("Profile must be mobile or fixed")
    return files("gps_rssi").joinpath("profiles/" + PROFILES[profile]).read_bytes()


def migrate_settings(path, *, profile):
    """Seed a missing role-named file from its legacy sibling, retaining both."""
    if path.name != PROFILES[profile] or path.exists():
        return
    legacy = path.with_name(LEGACY_PROFILES[profile])
    if not legacy.exists():
        return
    content = legacy.read_bytes()
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)  # Publish complete bytes without replacing a winner.
        except FileExistsError:
            return
    finally:
        Path(temporary).unlink(missing_ok=True)
    LOG.info("Copied legacy settings from %s to %s; original retained", legacy, path)


def load_settings(path, *, profile="mobile"):
    path = path.expanduser().resolve()
    template = profile_template(profile).decode("utf-8")
    migrate_settings(path, profile=profile)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(template)
            LOG.info("Created settings: %s", path)
        except FileExistsError:
            pass  # A simultaneous launch created it; never overwrite settings.
    result = tomllib.loads(template)
    with path.open("rb") as source:
        supplied = tomllib.load(source)
    for section, values in supplied.items():
        if section not in result or not isinstance(values, dict):
            raise ValueError(f"Unknown or invalid settings section: {section}")
        for key, value in values.items():
            if key not in result[section]:
                raise ValueError(f"Unknown setting: {section}.{key}")
            result[section][key] = value
    for section, keys in {"radio": ("address", "transport", "advertised_name"),
                          "recording": ("station", "database", "label", "role"),
                          "dashboard": ("area",)}.items():
        for key in keys:
            value = result[section][key]
            if not isinstance(value, str) or (not value.strip() and key not in ("label", "advertised_name")):
                raise ValueError(f"{section}.{key} must be text")
    for section, key in (("recording", "gps"), ("dashboard", "open_browser")):
        if type(result[section][key]) is not bool:
            raise ValueError(f"{section}.{key} must be true or false")
    for section, keys in {"recording": ("interval", "timeout", "connect_timeout", "reconnect_delay"),
                          "dashboard": ("refresh", "max_gps_age", "max_gps_accuracy")}.items():
        for key in keys:
            value = result[section][key]
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{section}.{key} must be a finite number greater than zero")
    for section, key, minimum, maximum in (("radio", "rfcomm_channel", 0, 30),
                                           ("dashboard", "port", 1, 65535),
                                           ("dashboard", "max_points", 1, 1000000)):
        value = result[section][key]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{section}.{key} must be an integer from {minimum} to {maximum}")
    if result["radio"]["transport"] not in ("ble", "rfcomm"):
        raise ValueError("radio.transport must be ble or rfcomm")
    if result["recording"]["role"] not in ("mobile", "fixed", "repeater"):
        raise ValueError("recording.role must be mobile, fixed, or repeater")
    if result["radio"]["transport"] == "rfcomm" and not result["radio"]["rfcomm_channel"]:
        raise ValueError("Set radio.rfcomm_channel to the discovered command-service channel")
    if result["dashboard"]["area"] not in ("la-habra-heights", "none"):
        raise ValueError("dashboard.area must be la-habra-heights or none")
    db = Path(result["recording"]["database"]).expanduser()
    result["recording"]["database"] = str((path.parent / db).resolve())
    return result


def setup_hints(directory, custom=None):
    """Read hints without creating personal files or modifying configuration."""
    paths = [(profile, directory / name, name) for profile, name in PROFILES.items()]
    if custom is not None and all(path.resolve() != custom.resolve() for _, path, _ in paths):
        paths.append(("custom", custom, None))
    hints, warnings = {}, []
    for profile, path, template in paths:
        try:
            if template and not path.exists():
                legacy = path.with_name(LEGACY_PROFILES[profile])
                if legacy.exists():
                    path = legacy  # Discovery stays read-only until selection.
            personal = path.exists()
            defaults = tomllib.loads(profile_template(profile).decode("utf-8")) if template else {}
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
        migrate_settings(path, profile=profile)
        original = path.read_bytes() if path.exists() else None
        template = profile_template(profile)
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
