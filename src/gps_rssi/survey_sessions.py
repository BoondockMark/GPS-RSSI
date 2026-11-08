"""Named field runs, operation ownership, and explicit finish/cleanup lifecycle."""

from copy import deepcopy
import json
from pathlib import Path
import re
import threading
import time
import uuid

from .measurements import utc_now


def session_path(root, key):
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError("Choose an existing survey session")
    root = Path(root).resolve()
    path = root / "data" / "workspace" / "sessions" / key / "session.json"
    if not path.resolve().is_relative_to(root):
        raise ValueError("Survey sessions must stay inside the app folder")
    return path


def session_metadata(path, config):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{32}", data["id"]) or data["state"] != "open":
        raise ValueError("Survey session is no longer open")
    if Path(config).name not in {p["id"] for p in data["radios"]}:
        raise ValueError("This radio is not part of the survey session")
    return dict(id=data["id"], name=data["name"], notes=data["notes"])


class SurveySessions:
    def __init__(self, root, jobs):
        self.root, self.jobs = Path(root).resolve(), jobs
        self.entries = {}
        directory = self.root / "data" / "workspace" / "sessions"
        if not directory.resolve().is_relative_to(self.root):
            raise ValueError("Survey sessions must stay inside the app folder")
        for path in sorted(directory.glob("*/session.json"), key=lambda p: p.stat().st_mtime):
            try:
                if path.resolve() != session_path(self.root, path.parent.name).resolve():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                if data["id"] != path.parent.name:
                    continue
                if data["state"] == "finishing":
                    data.update(state="attention", message="Finishing was interrupted. Check the radios in Activity, then finish again.")
                self.entries[data["id"]] = data
            except (OSError, ValueError, KeyError):
                continue

    def save(self, entry):
        path = session_path(self.root, entry["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(".session.json.tmp")
        temporary.write_text(json.dumps(entry, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)

    def require(self, key, profile=None, *, open_only=False):
        session_path(self.root, key)
        if key not in self.entries:
            raise ValueError("Survey session not found")
        entry = self.entries[key]
        if open_only and entry["state"] != "open":
            raise ValueError("This survey is closed to new operations. Create a new session.")
        if profile:
            member = next((p for p in entry["radios"] if p["id"] == profile["id"]), None)
            if not member:
                raise ValueError("Choose a radio belonging to this survey session")
            if open_only and member["revision"] != profile["revision"]:
                raise ValueError("Radio setup changed since this survey was created. Create a new session.")
        return entry

    def create(self, data, profiles):
        name, notes, selected = data.get("name"), data.get("notes", ""), data.get("profiles")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Enter a survey name of 1–80 characters")
        if not isinstance(notes, str) or len(notes) > 4000 or any(ord(c) < 32 and c not in "\n\t\r" for c in notes):
            raise ValueError("Survey notes must be text of at most 4000 characters")
        if not isinstance(selected, list) or not 1 <= len(selected) <= 20 or any(not isinstance(p, str) for p in selected):
            raise ValueError("Select between one and twenty saved radios")
        available = {p["id"]: p for p in profiles if not p.get("error")}
        if any(key not in available for key in selected):
            raise ValueError("Choose saved radios with valid setups")
        with self.jobs.lock:
            if self.jobs.closing:
                raise RuntimeError("The workspace is shutting down")
            keys = ("id", "name", "role", "address", "label", "gps", "database", "revision")
            entry = dict(id=uuid.uuid4().hex, name=name.strip(), notes=notes.strip(), created_at=utc_now(),
                finished_at=None, state="open", message="Ready for preflight. Creating a session does not start any radio.",
                radios=[{k: available[key][k] for k in keys} for key in dict.fromkeys(selected)], result_job=None)
            self.save(entry)
            self.entries[entry["id"]] = entry
            return self.describe(entry["id"])

    def operations(self, key):
        return [self.jobs.describe(job_id) for job_id, job in self.jobs.entries.items()
                if job.get("survey_session") == key and job["kind"] != "session-results"]

    def describe(self, key):
        with self.jobs.lock:
            entry = deepcopy(self.require(key))
            operations = self.operations(key)
            entry["operations"] = [{k: op.get(k) for k in ("id", "kind", "profile", "title", "state", "created_at", "finished_at", "message", "result")} for op in operations]
            entry["samples_recorded"] = sum((op.get("result") or {}).get("samples_recorded", 0) for op in operations)
            entry["tones_sent"] = sum((op.get("result") or {}).get("tones_sent", 0) for op in operations)
            entry["result"] = self.jobs.describe(entry["result_job"]) if entry.get("result_job") in self.jobs.entries else None
            if entry["result"] and entry["result"]["state"] == "completed":
                entry["samples_recorded"] = (entry["result"].get("result") or {}).get("selected_samples", entry["samples_recorded"])
            return entry

    def list(self):
        with self.jobs.lock:
            return [self.describe(key) for key in reversed(self.entries)]

    def finish(self, key):
        with self.jobs.lock:
            entry = self.require(key)
            if self.jobs.closing:
                raise RuntimeError("The workspace is shutting down")
            if entry["state"] == "finishing":
                return self.describe(key)
            result = self.jobs.entries.get(entry.get("result_job"))
            if result and result["state"] in ("running", "stopping", "unknown", "completed"):
                return self.describe(key)
            entry.update(state="finishing", message="Stopping this session's operations and waiting for cleanup…")
            self.save(entry)
            for operation in self.operations(key):
                if operation["state"] == "running":
                    self.jobs.stop(operation["id"])
            threading.Thread(target=self._finish, args=(key,), daemon=True).start()
            return self.describe(key)

    def _finish(self, key):
        deadline = time.monotonic() + 30
        try:
            while True:
                with self.jobs.lock:
                    operations = self.operations(key)
                    if not any(op["state"] in ("running", "stopping") for op in operations):
                        break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Radio cleanup is still pending. Check Activity before finishing again.")
                threading.Event().wait(.1)
            with self.jobs.lock:
                if any(op["state"] == "unknown" for op in operations):
                    raise RuntimeError("Radio stop is not verified. Check and acknowledge the affected operation in Activity, then finish again.")
                entry = self.entries[key]
                entry.update(state="finished", finished_at=entry.get("finished_at") or utc_now(), operations=operations,
                             message="Operations ended. Preparing this survey's results.")
                self.save(entry)
                result = self.jobs.launch("session-results", lambda out: ["session-results", "--directory", str(self.root),
                    "--session", key, "--output", str(out)], title="Survey results · " + entry["name"],
                    resources=["survey-session:" + key], survey_session=key)
                entry["result_job"] = result["id"]
                self.save(entry)
        except Exception as exc:
            with self.jobs.lock:
                entry = self.entries[key]
                entry.update(state="attention", message=str(exc))
                self.save(entry)
