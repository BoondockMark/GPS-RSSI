"""Local operator workflows over saved profiles, recordings, and existing commands."""

from contextlib import ExitStack
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import tomllib
import uuid
import wave

from .locking import InstanceLock
from .measurements import utc_now, timestamp
from .settings import load_settings, profile_template
from .sharing import check_log, readonly
from .storage import load_log
from .workspace_jobs import Jobs, read_json
from .offline import OfflineData
from .survey_sessions import SurveySessions, session_path


def text(data, key, default="", maximum=200):
    value = data.get(key, default)
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {key}")
    return value.strip()


def number(data, key, default, low, high):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{key} must be between {low:g} and {high:g}")
    return value


def radio_identity(address):
    compact = address.replace(":", "").replace("-", "").casefold()
    return compact if re.fullmatch(r"[0-9a-f]{12}", compact) else address.casefold()


class Workspace:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs = Jobs(self.root)
        self.sessions = SurveySessions(self.root, self.jobs)
        self.intents = {}
        self.port = None

    def inside(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Choose a file inside this app folder")
        return path

    def profiles(self):
        out = []
        for path in sorted(self.root.glob("*.toml")):
            if path.name == "pyproject.toml" or path.name.startswith(".") or path.is_symlink():
                continue
            try:
                supplied = tomllib.loads(path.read_text(encoding="utf-8"))
                if not ({"radio", "recording"} & supplied.keys()):
                    continue
                settings = load_settings(path)
                r, recording, dashboard = (settings[k] for k in ("radio", "recording", "dashboard"))
                database = self.inside(recording["database"])
                entry = dict(id=path.name, revision=hashlib.sha256(path.read_bytes()).hexdigest(),
                    name=recording["station"], role=recording["role"], label=recording["label"],
                    address=r["address"], advertised_name=r["advertised_name"], transport=r["transport"],
                    rfcomm_channel=r["rfcomm_channel"], gps=recording["gps"], port=dashboard["port"],
                    database=str(database.relative_to(self.root)), has_data=database.is_file(), latest=None, error=None)
                entry["telemetry_max_age_seconds"] = min(3600, max(30, recording["timeout"] * 3 + recording["interval"] * 3))
                entry["connect_timeout_seconds"] = recording["connect_timeout"]
                if database.is_file():
                    try:
                        _, samples, _ = load_log(database, station=recording["station"], source="live", sample_limit=1, event_limit=0, newest=True)
                        if samples:
                            sample = samples[-1]
                            entry["latest"] = {k: sample[k] for k in ("received_at", "rssi_value", "rssi_unit", "is_in_rx", "is_in_tx", "gps_locked")}
                    except Exception:
                        entry["data_error"] = "The recording could not be read. Check the file or its recorder."
                out.append(entry)
            except (ValueError, OSError, KeyError) as exc:
                out.append(dict(id=path.name, name=path.stem, error=str(exc), has_data=False))
        return out

    def profile(self, profile_id):
        matches = [p for p in self.profiles() if p["id"] == profile_id]
        if not matches or matches[0].get("error"):
            raise ValueError("Choose a saved radio with a valid setup")
        return matches[0]

    def radio_resources(self, profile):
        return ["radio:" + profile["address"].casefold(), "radio-id:" + radio_identity(profile["address"])]

    def save_profile(self, data):
        try:
            import tomlkit
        except ImportError as exc:
            raise RuntimeError("Radio setup editing needs the Bluetooth installation extras") from exc
        name, role = text(data, "name", maximum=80), text(data, "role", "mobile")
        address, label = text(data, "address"), text(data, "label", maximum=500)
        if not name or name.startswith("-") or not address or role not in ("mobile", "fixed"):
            raise ValueError("Enter a radio name, Bluetooth address, and mobile or fixed role")
        if type(data.get("gps", role == "mobile")) is not bool:
            raise ValueError("GPS must be on or off")
        existing_id = data.get("id")
        existing = self.profile(existing_id) if existing_id else None
        stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "radio"
        filename = existing_id if existing else stem + ".radio.toml"
        path = self.inside(self.root / filename)
        if not existing and path.exists():
            raise ValueError("A setup with this name already exists. Choose a different radio name.")
        profiles = self.profiles()
        used_ports = {p.get("port") for p in profiles}
        port = data.get("port", existing["port"] if existing else None)
        if port is None:
            port = next(p for p in range(8765, 8965) if p not in used_ports and p != self.port)
        number({"port": port}, "port", 8765, 1024, 65535)
        if type(port) is not int or port == self.port:
            raise ValueError("Choose a separate whole-number live-view port")
        database_value = text(data, "database", existing["database"] if existing else "data/" + stem + ".sqlite", 500)
        database = self.inside(self.root / database_value)
        if database.is_relative_to(self.root / "data" / "workspace") or database.is_dir():
            raise ValueError("Choose a separate recording file outside saved workspace results")
        for other in profiles:
            if other["id"] == existing_id or other.get("error"):
                continue
            if other["name"] == name or radio_identity(other["address"]) == radio_identity(address):
                raise ValueError("Each physical radio needs its own name and Bluetooth address")
            if other["port"] == port or self.inside(self.root / other["database"]) == database:
                raise ValueError("Each radio needs a separate recording file and live-view port")
        with self.jobs.lock, ExitStack() as stack:
            if not existing and path.exists():
                raise RuntimeError("A setup with this name was just created. Reload before continuing.")
            for other in self.profiles():
                if other["id"] == existing_id or other.get("error"):
                    continue
                other_db = self.inside(self.root / other["database"])
                if (other["name"] == name or radio_identity(other["address"]) == radio_identity(address) or other["port"] == port
                        or other_db == database or (other_db.is_file() and database.is_file() and other_db.samefile(database))):
                    raise RuntimeError("Another radio uses this name, address, recording file, or port. Choose distinct settings.")
            self.jobs.check_resources(self.radio_resources(existing or {"address": address}) + self.radio_resources({"address": address}))
            stack.enter_context(InstanceLock(path.with_name(path.name + ".app.lock")))
            original = path.read_bytes() if path.exists() else None
            revision = hashlib.sha256(original).hexdigest() if original else None
            if existing and data.get("revision") != revision:
                raise RuntimeError("This setup changed since you opened it. Reload before saving.")
            if existing:
                old_db = self.inside(self.root / existing["database"])
                stack.enter_context(InstanceLock(old_db.with_name(old_db.name + ".app.lock"),
                    message="Stop this radio's recorder before editing its setup"))
            doc = tomlkit.parse((original or profile_template(role)).decode("utf-8"))
            for section in ("radio", "recording", "dashboard"):
                if section not in doc:
                    doc[section] = tomlkit.table()
            doc["radio"]["address"] = address
            doc["radio"]["advertised_name"] = text(data, "advertised_name", (existing or {}).get("advertised_name", ""))
            doc["recording"].update(station=name, role=role, label=label,
                database=str(database.relative_to(self.root)), gps=data.get("gps", role == "mobile"))
            doc["dashboard"]["port"] = port
            fd, temporary = tempfile.mkstemp(prefix=".workspace-setup-", suffix=".toml", dir=self.root)
            temporary = Path(temporary)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(tomlkit.dumps(doc).encode("utf-8"))
                load_settings(temporary, profile=role)
                if (path.read_bytes() if path.exists() else None) != original:
                    raise RuntimeError("The setup changed while saving. Reload and try again.")
                if original:
                    backups = self.root / "setup-backups"
                    backups.mkdir(exist_ok=True)
                    backup = backups / (path.name + "-" + uuid.uuid4().hex + ".bak")
                    with backup.open("xb") as output:
                        output.write(original)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return self.profile(filename)

    def datasets(self):
        databases, audio = {}, {}
        for p in self.profiles():
            if p.get("has_data") and not p.get("error"):
                key = "profile:" + p["id"]
                databases[key] = dict(id=key, name=p["name"], path=p["database"], source="radio")
        imports = self.root / "data" / "workspace" / "imports"
        for path in imports.glob("*/import.json"):
            try:
                data = read_json(path)
                file = self.inside(path.parent / ("data.sqlite" if data["kind"] == "database" else "audio.wav"))
                if file.is_file():
                    key = "import:" + path.parent.name
                    target = databases if data["kind"] == "database" else audio
                    target[key] = dict(id=key, name=data["name"], path=str(file.relative_to(self.root)), source="import")
            except (OSError, ValueError, KeyError):
                continue
        with self.jobs.lock:
            for key, job in self.jobs.entries.items():
                if job["state"] != "completed":
                    continue
                output = self.jobs.folder(key) / "result"
                for filename in ("recordings.sqlite", "snapshot.sqlite"):
                    if (output / filename).is_file():
                        dataset_id = "job:" + key + ":" + filename
                        databases[dataset_id] = dict(id=dataset_id, name=job["title"],
                            path=str((output / filename).relative_to(self.root)), source="result")
                if job["kind"] == "capture" and (output / "audio.wav").is_file():
                    dataset_id = "job:" + key + ":audio.wav"
                    audio[dataset_id] = dict(id=dataset_id, name=job["title"],
                        path=str((output / "audio.wav").relative_to(self.root)), source="capture")
        return dict(databases=list(databases.values()), audio=list(audio.values()))

    def dataset(self, dataset_id, kind="databases"):
        matches = [d for d in self.datasets()[kind] if d["id"] == dataset_id]
        if not matches:
            raise ValueError("Select an available recording or audio capture")
        return self.inside(self.root / matches[0]["path"])

    def register_import(self, path, kind, name):
        if kind == "database":
            from contextlib import closing
            with closing(readonly(path)) as db:
                check_log(db)
        elif kind == "audio":
            with wave.open(str(path), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                    raise ValueError("Choose an uncompressed 16-bit mono WAV file")
        else:
            raise ValueError("Choose a recording database or received audio file")
        destination = self.root / "data" / "workspace" / "imports" / uuid.uuid4().hex
        destination.mkdir(parents=True)
        path.replace(destination / ("data.sqlite" if kind == "database" else "audio.wav"))
        safe_name = name.replace("\\", "/").split("/")[-1][:120]
        safe_name = "".join(c for c in safe_name if ord(c) >= 32) or "Imported file"
        (destination / "import.json").write_text(json.dumps(dict(kind=kind, name=safe_name, created_at=utc_now())), encoding="utf-8")
        return self.datasets()

    def state(self):
        return dict(offline_data=OfflineData(self.root/"data").status(), folder=str(self.root), profiles=self.profiles(), datasets=self.datasets(), jobs=self.jobs.list(), sessions=self.sessions.list(),
            capabilities=dict(bluetooth=importlib.util.find_spec("benlink") is not None,
                setup=importlib.util.find_spec("tomlkit") is not None,
                audio=all(importlib.util.find_spec(name) is not None for name in ("av", "numpy", "scipy"))))

    def tone_parameters(self, data):
        return dict(frequency=number(data, "frequency", 1000, 400, 2500),
                    duration=number(data, "duration", 3, .5, 10),
                    level_dbfs=number(data, "level_dbfs", -18, -36, -6))

    def prepare_transmit(self, data):
        profile = self.profile(data.get("profile"))
        from .audio_transport import mac_address
        mac_address(profile["address"])
        parameters = self.tone_parameters(data)
        automatic = data.get("automatic", False)
        if not isinstance(automatic, bool):
            raise ValueError("automatic must be true or false")
        if automatic:
            parameters.update(automatic=profile["role"], frequency=1000, duration=5)
        if data.get("field") is True:
            if not automatic:
                raise ValueError("Field surveys use automatic mode")
            recent = next((j for j in self.jobs.list() if j["kind"] == "preflight" and j["profile"] == profile["id"]
                           and j.get("survey_session") == data.get("survey_session")), None)
            result = (recent or {}).get("result") or {}
            if (not recent or recent["state"] != "completed" or result.get("ready") is not True
                    or result.get("profile_revision") != profile["revision"]
                    or not 0 <= (timestamp(utc_now()) - timestamp(result["checked_at"])).total_seconds() <= 120):
                raise ValueError("Run a successful preflight for this radio before starting (valid for two minutes)")
            parameters["recording"] = True
        with self.jobs.lock:
            survey_session = data.get("survey_session")
            if survey_session:
                if data.get("field") is not True:
                    raise ValueError("Named sessions use the field recording workflow")
                self.sessions.require(survey_session, profile, open_only=True)
                parameters["survey_session"] = survey_session
            self.jobs.check_resources(self.radio_resources(profile))
            now = time.monotonic()
            self.intents = {key: value for key, value in self.intents.items() if value["expires"] > now}
            key = uuid.uuid4().hex
            self.intents[key] = dict(profile=profile, parameters=parameters, expires=now + 60)
        return dict(intent=key, radio=profile["name"], address=profile["address"], parameters=parameters, expires_seconds=60,
                    survey_name=self.sessions.require(survey_session)["name"] if survey_session else None)

    def launch(self, data):
        with self.jobs.lock:
            return self._launch(data)

    def _launch(self, data):
        kind = data.get("kind")
        if kind == "scan":
            return self.jobs.launch(kind, lambda _: ["scan", "--json"], title="Find nearby radios")
        if kind == "transmit":
            with self.jobs.lock:
                intent = self.intents.pop(data.get("intent"), None)
                if data.get("confirmed") is not True:
                    raise ValueError("Confirm the radio's channel and power before transmitting")
                if not intent or intent["expires"] <= time.monotonic():
                    raise ValueError("This transmission review expired or was already used. Cancel and review it again.")
                profile = self.profile(intent["profile"]["id"])
                if profile["revision"] != intent["profile"]["revision"]:
                    raise RuntimeError("The radio setup changed. Review the transmission again.")
                parameters = intent["parameters"]
        elif kind in ("record", "capture", "services", "preflight"):
            profile = self.profile(data.get("profile"))
            parameters = {}
        else:
            profile, parameters = None, {}
        if profile:
            resources = self.radio_resources(profile)
            survey_session = parameters.get("survey_session") if kind == "transmit" else data.get("survey_session")
            session_options = {}
            if survey_session:
                if kind not in ("preflight", "transmit"):
                    raise ValueError("Named sessions use field preflight and recording")
                self.sessions.require(survey_session, profile, open_only=True)
                session_options["survey_session"] = survey_session
            if kind == "preflight":
                resources += ["database:" + str(self.inside(self.root / profile["database"]))]
                return self.jobs.launch(kind, lambda out: ["field-check", "--config", str(self.root / profile["id"]), "--output", str(out)],
                    title="Field preflight · " + profile["name"], profile=profile["id"], resources=resources, **session_options)
            if kind == "record":
                if profile["port"] == self.port:
                    raise ValueError("This radio's live-view port conflicts with the workspace; edit its advanced setup")
                resources += ["database:" + str(self.inside(self.root / profile["database"])), "port:" + str(profile["port"])]
                return self.jobs.launch(kind, lambda out: ["start", "--config", str(self.root / profile["id"]), "--no-browser", "--status-output", str(out)],
                    title="Recording · " + profile["name"], profile=profile["id"], resources=resources)
            if kind == "services":
                return self.jobs.launch(kind, lambda _: ["audio-services", "--address", profile["address"], "--json"],
                    title="Check audio · " + profile["name"], profile=profile["id"], resources=resources)
            if kind == "capture":
                duration = number(data, "duration", 30, 1, 600)
                args = lambda out: ["audio-receive", "--address", profile["address"], "--station", profile["name"],
                                    "--duration", str(duration), "--output", str(out)]
                return self.jobs.launch(kind, args, title="Receive capture · " + profile["name"],
                    profile=profile["id"], resources=resources, duration=duration)
            if kind == "transmit":
                if parameters.get("recording"):
                    resources += ["database:" + str(self.inside(self.root / profile["database"]))]
                args = lambda out: ["tone", "--transmit", "--address", profile["address"], "--output", str(out),
                    "--frequency", str(parameters["frequency"]), "--duration", str(parameters["duration"]),
                    "--level-dbfs", str(parameters["level_dbfs"])] + (
                        ["--automatic", parameters["automatic"]] if parameters.get("automatic") else []) + (
                        ["--record-config", str(self.root / profile["id"])] if parameters.get("recording") else []) + (
                        ["--survey-session", str(session_path(self.root, survey_session))] if survey_session else [])
                return self.jobs.launch(kind, args, title=("Field survey · " if parameters.get("recording") else "Automatic tones · " if parameters.get("automatic") else "Transmit tone · ") + profile["name"],
                    profile=profile["id"], resources=resources, duration=None if parameters.get("automatic") else parameters["duration"], **session_options)
        if kind == "preview":
            parameters = self.tone_parameters(data)
            return self.jobs.launch(kind, lambda out: ["tone", "--output", str(out), "--frequency", str(parameters["frequency"]),
                "--duration", str(parameters["duration"]), "--level-dbfs", str(parameters["level_dbfs"])], title="Test tone preview")
        if kind == 'offline-export':
            return self.jobs.launch(kind,lambda out:['offline','--directory',str(self.root),'export','--output',str(out/'offline-data.zip')],title='Portable offline data',make_output=True,resources=['offline-export'])
        if kind == 'offline-fill':
            if OfflineData(self.root/'data').enabled:
                raise ValueError('Turn off Offline mode before filling missing data')
            return self.jobs.launch(kind,lambda out:['offline','--directory',str(self.root),'fill','--output',str(out)],title='Fill missing offline data',make_output=True,resources=['offline-fill'])
        if kind == "demo":
            return self.jobs.launch(kind, lambda out: ["demo", "--output", str(out)], title="Synthetic test survey")
        if kind in ("survey", "merge"):
            ids = data.get("databases")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or any(not isinstance(key, str) for key in ids):
                raise ValueError("Select between 1 and 20 recording files")
            paths = list(dict.fromkeys(self.dataset(key) for key in ids))
            source = data.get("source", "live")
            if source not in ("live", "replay", "all"):
                raise ValueError("Choose live, replay, or all data")
            def args(out):
                result = [kind]
                for path in paths:
                    result += ["--db", str(path)]
                return result + ["--output", str(out if kind == "survey" else out / "recordings.sqlite")] + (["--source", source] if kind == "survey" else [])
            return self.jobs.launch(kind, args, title="Combined survey" if kind == "survey" else "Merged recordings", make_output=kind == "merge")
        if kind in ("export", "share"):
            path = self.dataset(data.get("database"))
            format = data.get("format", "gpkg")
            if format not in ("csv", "geojson", "gpkg", "kml", "gpx"):
                raise ValueError("Choose an available export format")
            source = data.get("source", "live")
            if source not in ("live", "replay", "all"):
                raise ValueError("Choose an available data source")
            def args(out):
                return [kind, "--db", str(path), "--output", str(out / ("snapshot.sqlite" if kind == "share" else "measurements." + format))] + (
                    ["--source", source] if kind == "export" else [])
            return self.jobs.launch(kind, args, title="Portable recording" if kind == "share" else format.upper() + " export", make_output=True)
        if kind == "analyze":
            path = self.dataset(data.get("audio"), "audio")
            frequency = number(data, "frequency", 1000, 400, 2500)
            return self.jobs.launch(kind, lambda out: ["sinad", "--input", str(path), "--output", str(out),
                "--frequency", str(frequency)], title="Signal quality analysis")
        raise ValueError("Choose an available workspace action")
