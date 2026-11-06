"""Supervise independent mobile/fixed recorders using existing app profiles."""

import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .settings import load_settings

LOG = logging.getLogger(__name__)


def validate_fleet(paths):
    """Reject conflicting resources before connecting to any Bluetooth radio."""
    configs, seen = [], {key: set() for key in ("address", "database", "port", "station")}
    databases = []
    for path in paths:
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Create this transceiver's setup before launching the fleet: {path}")
        settings = load_settings(path)
        database = Path(settings["recording"]["database"])
        values = dict(address=settings["radio"]["address"].casefold(), database=os.path.normcase(str(database)),
                      port=settings["dashboard"]["port"], station=settings["recording"]["station"])
        for key, value in values.items():
            if value in seen[key]:
                raise ValueError(f"Each transceiver needs a distinct {key}; duplicate in {path.name}: {value}")
            seen[key].add(value)
        if database.exists() and any(p.exists() and p.samefile(database) for p in databases):
            raise ValueError("Each transceiver needs a separate database file")
        databases.append(database)
        configs.append((path, settings))
    if not configs:
        raise ValueError("Supply at least one transceiver setup")
    return configs


def stop_recorders(processes, *, grace=15):
    """Ask every child to disconnect and commit before bounded forced cleanup."""
    for process in processes:
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGINT)
            except OSError as exc:
                LOG.warning("Could not signal recorder PID %s for graceful cleanup: %s", process.pid, exc)
    deadline = time.monotonic() + grace
    while any(p.poll() is None for p in processes) and time.monotonic() < deadline:
        time.sleep(.1)
    for process in processes:
        if process.poll() is None:
            LOG.warning("Recorder did not finish disconnect cleanup; terminating PID %s", process.pid)
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def start_fleet(paths, *, no_browser=False):
    configs = validate_fleet(paths)
    processes = []
    try:
        for path, settings in configs:
            command = [sys.executable, "-m", "gps_rssi.cli", "start", "--config", str(path)]
            if no_browser:
                command.append("--no-browser")
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            processes.append(subprocess.Popen(command, **options))
            LOG.info("Started %s | %s | dashboard port %s", settings["recording"]["station"], path,
                     settings["dashboard"]["port"])
        LOG.info("Fleet running. One Ctrl-C stops all recorders. Each dashboard's Exit stops only that transceiver.")
        while any(p.poll() is None for p in processes):
            for process in processes:
                code = process.poll()
                if code not in (None, 0):
                    raise RuntimeError(f"Recorder PID {process.pid} exited with status {code}; stopping the fleet")
            time.sleep(.2)
        # A child may fail between the last status scan and the loop condition.
        if any(p.returncode != 0 for p in processes):
            raise RuntimeError("A recorder failed; check the preceding diagnostics")
    except KeyboardInterrupt:
        LOG.info("Stopping all transceivers…")
    finally:
        stop_recorders(processes)
