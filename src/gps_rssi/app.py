"""One-process radio logger and dashboard with persistent local settings."""

import asyncio
import errno
from importlib.resources import files
import logging
import math
import os
from pathlib import Path
import threading
import tomllib
import webbrowser

from .btech import collect, connection
from .controls import Controls
from .live import make_server
from .storage import Store

LOG = logging.getLogger(__name__)


def load_settings(path):
    path = path.expanduser().resolve()
    template = files("gps_rssi").joinpath("assets/uvpro.toml").read_text(encoding="utf-8")
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
    for section, keys in {"radio": ("address", "transport"),
                          "recording": ("station", "database", "label"),
                          "dashboard": ("area",)}.items():
        for key in keys:
            value = result[section][key]
            if not isinstance(value, str) or (not value.strip() and key != "label"):
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
    if result["radio"]["transport"] == "rfcomm" and not result["radio"]["rfcomm_channel"]:
        raise ValueError("Set radio.rfcomm_channel to the discovered command-service channel")
    if result["dashboard"]["area"] not in ("la-habra-heights", "none"):
        raise ValueError("dashboard.area must be la-habra-heights or none")
    db = Path(result["recording"]["database"]).expanduser()
    result["recording"]["database"] = str((path.parent / db).resolve())
    return result


class InstanceLock:
    """OS-owned lock; automatically released if the app exits or crashes."""
    def __init__(self, path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("This database is already in use by another start command. "
                               "Use its existing window/dashboard instead.") from exc
        return self

    def __exit__(self, *args):
        self.file.close()  # Do not unlink: another process may be waiting on this inode.


async def run_together(store, factory, server, settings, *, browser=True, controls=None):
    """Keep BLE on the main event loop; HTTP uses its own read-only connections."""
    loop = asyncio.get_running_loop()
    controls = controls or Controls(loop)
    server.controls = controls
    server_done = loop.create_future()

    def finished(error):
        if not server_done.done():
            if error:
                server_done.set_exception(error)
            else:
                server_done.set_result(None)

    def serve_dashboard():
        try:
            server.serve_forever()
        except Exception as exc:
            loop.call_soon_threadsafe(finished, exc)
        else:
            loop.call_soon_threadsafe(finished, None)

    thread = threading.Thread(target=serve_dashboard, name="gps-rssi-dashboard", daemon=True)
    thread.start()
    r = settings["recording"]
    def begin_collection():
        return asyncio.create_task(collect(store, factory, gps=r["gps"], interval=r["interval"],
            timeout=r["timeout"], connect_timeout=r["connect_timeout"], reconnect_delay=r["reconnect_delay"]))

    async def stop_collection():
        logger.cancel()
        try:
            await asyncio.shield(logger)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise

    logger = begin_collection()
    action_task = asyncio.create_task(controls.queue.get())
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        LOG.info("Dashboard: %s", url)
        LOG.info("Use Exit in the dashboard or press Ctrl-C once to stop. Closing the browser does not stop recording.")
        if browser:
            try:
                if not webbrowser.open(url, new=2):
                    LOG.warning("Browser did not open automatically; open %s manually", url)
            except Exception as exc:
                LOG.warning("Could not open browser: %s; open %s manually", exc, url)
        while True:
            done, _ = await asyncio.wait((logger, server_done, action_task), return_when=asyncio.FIRST_COMPLETED)
            if server_done in done:
                await server_done
                raise RuntimeError("Dashboard stopped unexpectedly; stopping the radio logger")
            if logger in done:
                await logger
                break
            operation = action_task.result()
            controls.update("running", "Stopping collection and disconnecting the radio…")
            await stop_collection()
            if operation["action"] == "exit":
                store.event("stopped", {"reason": "dashboard_exit"})
                controls.update("completed", "Recording stopped and database saved. You can close this tab.")
                controls.close()
                # Keep the final response available until the browser sees it,
                # with a bounded grace period if the tab/client went away.
                try:
                    await asyncio.wait_for(controls.ack.wait(), 2)
                except TimeoutError:
                    pass
                break
            controls.update("running", "Backing up the database and starting a fresh session…")
            try:
                backup = store.reset()
            except Exception as exc:
                LOG.warning("Database reset canceled; original data retained: %s", exc)
                controls.update("failed", f"Reset canceled; original data retained. {exc}")
            else:
                LOG.info("Database reset; backup: %s", backup)
                controls.update("completed", "Database reset. Recording is restarting.", backup=str(backup))
            logger = begin_collection()
            action_task = asyncio.create_task(controls.queue.get())
    finally:
        controls.close()
        if controls.status()["state"] in ("pending", "running"):
            controls.update("failed", "The app stopped before this operation finished. Check the console and database.")
        action_task.cancel()
        if not logger.done() and not logger.cancelling():
            logger.cancel()
        await asyncio.gather(logger, action_task, return_exceptions=True)
        await asyncio.to_thread(server.shutdown)
        thread.join(timeout=5)
        server.server_close()
        # Consume/cancel the future, including when the server failed during cancellation.
        if server_done.done() and not server_done.cancelled():
            server_done.exception()
        else:
            server_done.cancel()


def start(path, *, no_browser=False):
    settings = load_settings(path)
    radio, recording, dashboard = (settings[k] for k in ("radio", "recording", "dashboard"))
    db = Path(recording["database"])
    factory = lambda: connection(radio["address"], radio["transport"], radio["rfcomm_channel"] or None,
                                 connect_timeout=recording["connect_timeout"])
    with InstanceLock(db.with_name(db.name + ".app.lock")):
        factory()  # Validate optional dependency before starting any service.
        LOG.info("Settings: %s | Radio: %s | Database: %s", path.resolve(), radio["address"], db)
        store = Store(db, recording["station"], "mobile", "btech_bluetooth", settings)
        server = None
        try:
            try:
                server = make_server(db, port=dashboard["port"], station=recording["station"],
                    source="live", refresh=dashboard["refresh"], area=dashboard["area"],
                    max_points=dashboard["max_points"], max_age=dashboard["max_gps_age"],
                    max_accuracy=dashboard["max_gps_accuracy"])
            except OSError as exc:
                if exc.errno in (errno.EADDRINUSE, 10048) or getattr(exc, "winerror", None) == 10048:
                    raise RuntimeError(f"Dashboard port {dashboard['port']} is busy. Stop the old serve/start "
                                       f"window or change dashboard.port in {path.resolve()}") from exc
                raise
            asyncio.run(run_together(store, factory, server, settings,
                                    browser=dashboard["open_browser"] and not no_browser))
        except KeyboardInterrupt:
            store.event("stopped", {"reason": "keyboard_interrupt"})
        except Exception as exc:
            store.event("error", {"error": str(exc), "type": type(exc).__name__})
            raise
        finally:
            if server:
                server.server_close()
            store.close()
