"""Completed named results and comparison inputs, independent of radio acquisition."""

from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
import threading

from .result_metrics import result_quality, with_gps_coverage
from .survey_sessions import session_path


def result_folder(root, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Choose an existing result")
    root = Path(root).resolve()
    folder = root / "data" / "workspace" / "jobs" / job_id / "result"
    if not folder.resolve().is_relative_to(root):
        raise ValueError("Results must stay inside the app folder")
    return folder


def completed_source(root, session_id, job_id):
    manifest = json.loads(session_path(root, session_id).read_text(encoding="utf-8"))
    folder = result_folder(root, job_id)
    operation = json.loads((folder.parent / "operation.json").read_text(encoding="utf-8"))
    if (manifest["id"] != session_id or manifest["state"] != "finished" or manifest.get("result_job") != job_id
            or operation["id"] != job_id or operation["state"] != "completed" or operation["kind"] != "session-results"):
        raise ValueError("Select finished sweeps whose results are ready")
    database = folder / "recordings.sqlite"
    if not database.is_file() or not database.resolve().is_relative_to(folder.resolve()):
        raise ValueError("This sweep has no available recording snapshot")
    summary_path = folder / "summary.json"
    if not summary_path.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Result summaries must stay inside their result folder")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("mapped_points", 0) <= 0:
        raise ValueError("Location comparison requires mapped mobile receive points in both sweeps")
    return manifest, database


class ResultsLibrary:
    def __init__(self, root, jobs, sessions):
        self.root, self.jobs, self.sessions = Path(root).resolve(), jobs, sessions
        self.cache, self.cache_lock = {}, threading.Lock()

    def quality(self, session, folder):
        summary_path, database = folder / "summary.json", folder / "recordings.sqlite"
        if not summary_path.resolve().is_relative_to(folder.resolve()) or not database.resolve().is_relative_to(folder.resolve()):
            raise ValueError("Result summaries and recordings must stay inside their result folder")
        stamp = (summary_path.stat().st_mtime_ns, database.stat().st_mtime_ns if database.is_file() else None)
        key = str(folder)
        with self.cache_lock:
            if key in self.cache and self.cache[key][0] == stamp:
                return deepcopy(self.cache[key][1])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            quality = summary.get("result_quality")
            if quality is None:
                quality = with_gps_coverage(result_quality(database if database.is_file() else None,
                    session.get("operations", []), summary.get("source", "live")), summary.get("mapped_points", 0))
            self.cache[key] = (stamp, quality)
            return deepcopy(quality)

    def list(self):
        # Copy metadata under the jobs lock; database summaries are read outside it.
        with self.jobs.lock:
            sessions = [entry for entry in self.sessions.list() if entry["state"] == "finished"
                        and entry.get("result") and entry["result"]["state"] == "completed"]
            comparisons = [self.jobs.describe(key) for key, job in reversed(list(self.jobs.entries.items()))
                           if job["kind"] == "comparison"]
        results = []
        for session in sessions:
            item = {key: session[key] for key in ("id", "name", "notes", "sweep_type", "created_at", "finished_at", "radios", "result")}
            item["result"] = {key: item["result"][key] for key in ("id", "state", "title", "files")}
            item.update(quality=None, compare_available=False, comparison_reason=None, problem=None)
            try:
                folder = result_folder(self.root, item["result"]["id"])
                item["quality"] = self.quality(session, folder)
                item["compare_available"] = (folder / "recordings.sqlite").is_file() and item["quality"]["gps_points"] > 0
                if not item["compare_available"]:
                    item["comparison_reason"] = ("No recording snapshot available" if not (folder / "recordings.sqlite").is_file()
                        else "Comparison needs mapped mobile receive points")
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                item["problem"] = f"Summary unavailable: {exc}"
            results.append(item)
        return dict(results=results, comparisons=comparisons)

    def compare(self, selected):
        if (not isinstance(selected, list) or len(selected) != 2 or any(not isinstance(key, str) for key in selected)
                or selected[0] == selected[1]):
            raise ValueError("Select two different completed sweeps")
        with self.jobs.lock:
            sources, names = [], []
            for key in selected:
                session = self.sessions.require(key)
                job_id = session.get("result_job")
                job = self.jobs.entries.get(job_id)
                if session["state"] != "finished" or not job or job["state"] != "completed":
                    raise ValueError("Wait for both sweeps' results to finish")
                description = self.jobs.describe(job_id)
                if "recordings.sqlite" not in description["files"] or (description.get("result") or {}).get("mapped_points", 0) <= 0:
                    raise ValueError("Both sweeps need mapped mobile receive points for location comparison")
                sources.append(dict(session_id=key, result_job=job_id))
                names.append(session["name"])
            for key, entry in self.jobs.entries.items():
                if entry["kind"] == "comparison" and entry.get("comparison_sources") == sources:
                    existing = self.jobs.describe(key)
                    if entry["state"] in ("running", "stopping", "unknown") or (entry["state"] == "completed" and "map.html" in existing["files"]):
                        return existing
            return self.jobs.launch("comparison", lambda out: ["compare-results", "--directory", str(self.root),
                "--first", sources[0]["session_id"], "--first-result", sources[0]["result_job"],
                "--second", sources[1]["session_id"], "--second-result", sources[1]["result_job"], "--output", str(out)],
                title="Compare · " + names[0] + " ↔ " + names[1],
                resources=["comparison:" + ":".join(sorted(s["result_job"] for s in sources))], comparison_sources=sources)
