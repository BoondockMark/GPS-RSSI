"""Supervise existing CLI operations for the local operator workspace."""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from .measurements import utc_now

ACTIVE = {"running", "stopping", "unknown"}


def read_json(path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Operation metadata is too large")
    return json.loads(path.read_text(encoding="utf-8"))


class Jobs:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / "data" / "workspace" / "jobs"
        if not self.directory.resolve().is_relative_to(self.root):
            raise ValueError("Workspace results must stay inside the app folder")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.entries = {}
        self.processes = {}
        self.result_summaries = {}
        self.closing = False
        for path in sorted(self.directory.glob("*/operation.json"), key=lambda p: p.stat().st_mtime):
            try:
                if not path.resolve().is_relative_to(self.directory.resolve()):
                    continue
                entry = read_json(path)
                if entry["id"] != path.parent.name or not re.fullmatch(r"[0-9a-f]{32}", entry["id"]):
                    continue
                if entry["state"] in ACTIVE:
                    entry.update(state="unknown", message="The previous workspace ended. Check that this radio is stopped before continuing.")
                self.entries[entry["id"]] = entry
            except (OSError, ValueError, KeyError):
                continue

    def folder(self, job_id):
        if job_id not in self.entries:
            raise ValueError("Operation not found")
        return self.directory / job_id

    def _save(self, entry):
        folder = self.directory / entry["id"]
        temporary = folder / ".operation.json.tmp"
        temporary.write_text(json.dumps(entry, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(folder / "operation.json")

    def check_resources(self, resources):
        for entry in self.entries.values():
            if entry["state"] in ACTIVE and set(resources) & set(entry.get("resources", [])):
                if entry["state"] == "unknown":
                    raise RuntimeError("A previous operation needs attention. Verify the radio is stopped and acknowledge it in Activity.")
                raise RuntimeError("This radio or recording is already in use. Stop its current operation first.")

    def launch(self, kind, arguments, *, title, profile=None, resources=(), duration=None, make_output=False, survey_session=None, comparison_sources=None):
        """arguments is a trusted builder, never a client-supplied command line."""
        with self.lock:
            if self.closing:
                raise RuntimeError("The workspace is shutting down")
            self.check_resources(resources)
            job_id = uuid.uuid4().hex
            folder = self.directory / job_id
            folder.mkdir()
            output = folder / "result"
            if make_output:
                output.mkdir()
            args = arguments(output)
            entry = dict(id=job_id, kind=kind, title=title, profile=profile, state="running",
                         created_at=utc_now(), finished_at=None, resources=list(resources), duration=duration,
                         stop_requested=False, forced=False, message="Working…")
            if survey_session:
                entry["survey_session"] = survey_session
            if comparison_sources:
                entry["comparison_sources"] = comparison_sources
            self.entries[job_id] = entry
            self._save(entry)
            try:
                options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
                with (folder / "process.log").open("wb") as log:
                    process = subprocess.Popen([sys.executable, "-u", "-m", "gps_rssi.cli", *args],
                        cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                        env={**os.environ, "PYTHONIOENCODING": "utf-8",
                             "GPS_RSSI_MAP_DATA_DIRECTORY": str(self.root / "data")}, **options)
                self.processes[job_id] = process
            except Exception as exc:
                entry.update(state="failed", message=str(exc), finished_at=utc_now())
                self._save(entry)
                raise
            threading.Thread(target=self._watch, args=(job_id, process), daemon=True).start()
            return self.describe(job_id)

    def _watch(self, job_id, process):
        code = process.wait()
        with self.lock:
            entry = self.entries[job_id]
            entry["exit_code"] = code
            report = None
            path = self.folder(job_id) / "result" / "report.json"
            if path.is_file():
                try:
                    report = read_json(path)
                except (OSError, ValueError):
                    pass
            if entry["forced"]:
                entry.update(state="unknown" if entry["kind"] == "transmit" else "failed", message="The operation did not finish cleanup. Check the radio before continuing.")
            elif entry["kind"] == "transmit" and report and (report.get("stop_attempted") or report.get("transmission_started_at")
                    or report.get("state") == "transmitting") and report.get("stop_verified") is not True:
                entry.update(state="unknown", message="Radio stop was not verified. Check the transmitter and stop it manually if needed.")
            elif entry["kind"] == "transmit" and not report:
                entry.update(state="unknown", message="The transmit process ended without a final report. Check the radio before continuing.")
            elif entry["stop_requested"] and code in (0, 130, -signal.SIGINT):
                entry.update(state="stopped", message="Stopped. Saved data is retained.")
            elif code == 0:
                entry.update(state="completed", message="Recording ended. Your data is saved." if entry["kind"] == "record" else "Ready to view or download.")
            else:
                entry.update(state="failed", message=(report or {}).get("error") or self._tail(job_id).splitlines()[-1:] or "The operation failed. Open details for the recorded error.")
                if isinstance(entry["message"], list):
                    entry["message"] = entry["message"][0]
            entry["finished_at"] = utc_now()
            if entry["kind"] in ("scan", "services") and code == 0:
                try:
                    raw = (self.folder(job_id) / "process.log").read_text(encoding="utf-8")[:1024 * 1024]
                    start = raw.find("{" if entry["kind"] == "scan" else "[")
                    entry["discovery"] = json.JSONDecoder().raw_decode(raw[start:])[0]
                except (OSError, ValueError):
                    entry.update(state="failed", message="Discovery returned an unreadable result. Check details and retry.")
            self._save(entry)

    def _tail(self, job_id):
        path = self.folder(job_id) / "process.log"
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 8000))
                return stream.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def describe(self, job_id):
        with self.lock:
            entry = dict(self.entries[job_id])
            output = self.folder(job_id) / "result"
            entry.pop("resources", None)
            entry["log"] = self._tail(job_id)
            entry["files"] = [p.name for p in sorted(output.glob("*")) if p.is_file() and not p.is_symlink()]
            report_path = output / ("summary.json" if entry["kind"] in ("survey", "session-results", "comparison") else "report.json")
            if job_id in self.result_summaries and entry["state"] not in ACTIVE:
                entry["result"] = dict(self.result_summaries[job_id])
            elif report_path.is_file():
                try:
                    report = read_json(report_path)
                    entry["result"] = {k: report.get(k) for k in ("kind", "state", "stop_attempted", "stop_verified",
                        "cleanup_errors", "valid_windows", "window_count", "decoded_samples", "station",
                        "automatic", "role", "clock_source", "clock_fallback", "next_tone_at", "tones_sent",
                        "recording", "samples_recorded", "latest_telemetry", "preflight_passed",
                        "ready", "checks", "checked_at", "profile_revision", "clock_at", "next_tone_host_at", "reported_at",
                        "recording_session_id", "survey_session_id", "selected_samples", "expires_at", "phase",
                        "attempt", "error", "error_stage", "cleanup_error", "retry_at", "sweep_type", "result_quality", "comparison") if k in report}
                    for key in ("mapped_points", "receive_observations", "station_count"):
                        if key in report:
                            entry["result"][key] = report[key]
                    if entry["state"] not in ACTIVE:
                        self.result_summaries[job_id] = dict(entry["result"])
                except (OSError, ValueError):
                    pass
            if entry["kind"] in ("scan", "services") and entry["state"] == "completed":
                entry["result"] = entry.get("discovery")
            entry.pop("discovery", None)
            return entry

    def list(self):
        with self.lock:
            keys = list(reversed(self.entries))
            selected = list(dict.fromkeys([key for key in keys if self.entries[key]["state"] in ACTIVE] + keys[:100]))
            return [self.describe(key) for key in selected]

    def stop(self, job_id):
        with self.lock:
            if job_id not in self.entries:
                raise ValueError("Operation not found")
            entry = self.entries[job_id]
            if entry["state"] not in ("running", "stopping"):
                return self.describe(job_id)
            if not entry["stop_requested"]:
                entry.update(state="stopping", stop_requested=True,
                    message="Stopping and saving…" if entry["kind"] != "transmit" else "Stopping tone; waiting for radio stop verification…")
                self._save(entry)
                process = self.processes[job_id]
                try:
                    if os.name == "nt":
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(process.pid, signal.SIGINT)
                except OSError as exc:
                    entry["message"] = "The stop signal failed; cleanup will be retried. Check the radio."
                    entry["signal_error"] = str(exc)
                    self._save(entry)
                threading.Thread(target=self._stop_deadline, args=(job_id, process), daemon=True).start()
            return self.describe(job_id)

    def _stop_deadline(self, job_id, process):
        try:
            process.wait(timeout=15)
            return
        except subprocess.TimeoutExpired:
            pass
        with self.lock:
            if process.poll() is not None:
                return
            self.entries[job_id]["forced"] = True
        try:
            process.terminate()
        except OSError as exc:
            with self.lock:
                self.entries[job_id].update(state="unknown", message="Could not stop the process. Check the operation and radio.", signal_error=str(exc))
                self._save(self.entries[job_id])
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError as exc:
                with self.lock:
                    self.entries[job_id].update(state="unknown", message="Could not stop the process. Check the operation and radio.", signal_error=str(exc))
                    self._save(self.entries[job_id])

    def acknowledge(self, job_id, confirmed):
        with self.lock:
            if job_id not in self.entries or self.entries[job_id]["state"] != "unknown" or confirmed is not True:
                raise ValueError("Confirm that the previous operation and radio have stopped")
            process = self.processes.get(job_id)
            if process is not None and process.poll() is None:
                raise RuntimeError("This process is still running. Stop it before confirming that the operation ended.")
            self.entries[job_id].update(state="interrupted", message="You confirmed that the previous operation has stopped.")
            self._save(self.entries[job_id])

    def close(self):
        with self.lock:
            self.closing = True
            active = [key for key, entry in self.entries.items() if entry["state"] in ("running", "stopping")]
        for key in active:
            self.stop(key)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with self.lock:
                if all(self.entries[key]["state"] not in ("running", "stopping") for key in active):
                    return
            time.sleep(.1)
