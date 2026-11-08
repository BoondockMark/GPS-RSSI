"""Compose and supervise the radio collector and dashboard lifecycle."""

import asyncio
import errno
import logging
from pathlib import Path
import threading
import webbrowser

from .benlink_radio import collect, create_connection
from .controls import Controls
from .dashboard import make_server
from .locking import InstanceLock
from .settings import PROFILES, load_settings
from .storage import Store
from .operation_status import OperationStatus

LOG = logging.getLogger(__name__)


async def run_together(store, factory, server, settings, *, browser=True, controls=None, status=None):
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
    recording = settings["recording"]
    def begin_collection():
        options = {}
        if status:
            def update(**values):
                status.update(recording_session_id=store.session_id, **values)
            options["on_status"] = update
        async def run_collection():
            if status:
                status.update(state="connecting", samples_recorded=0, latest_telemetry=None,
                              recording_session_id=store.session_id,
                              valid_for=recording["connect_timeout"] + recording["timeout"] + 5)
            return await collect(store, factory, gps=recording["gps"], interval=recording["interval"],
                timeout=recording["timeout"], connect_timeout=recording["connect_timeout"], reconnect_delay=recording["reconnect_delay"], **options)
        return asyncio.create_task(run_collection())

    async def stop_collection():
        collector_task.cancel()
        try:
            await asyncio.shield(collector_task)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise

    collector_task = begin_collection()
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
            done, _ = await asyncio.wait((collector_task, server_done, action_task), return_when=asyncio.FIRST_COMPLETED)
            if server_done in done:
                await server_done
                raise RuntimeError("Dashboard stopped unexpectedly; stopping the radio logger")
            if collector_task in done:
                await collector_task
                break
            operation = action_task.result()
            if status:
                status.update(state="stopping", phase=operation["action"])
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
            collector_task = begin_collection()
            action_task = asyncio.create_task(controls.queue.get())
    finally:
        controls.close()
        if controls.status()["state"] in ("pending", "running"):
            controls.update("failed", "The app stopped before this operation finished. Check the console and database.")
        action_task.cancel()
        if not collector_task.done() and not collector_task.cancelling():
            collector_task.cancel()
        await asyncio.gather(collector_task, action_task, return_exceptions=True)
        await asyncio.to_thread(server.shutdown)
        thread.join(timeout=5)
        server.server_close()
        # Consume/cancel the future, including when the server failed during cancellation.
        if server_done.done() and not server_done.cancelled():
            server_done.exception()
        else:
            server_done.cancel()
        if status:
            status.update(state="stopping", phase="cleanup")


def start(path=None, *, profile="mobile", no_browser=False, status_output=None):
    if profile not in PROFILES:
        raise ValueError("Profile must be mobile or fixed")
    path = Path(path) if path is not None else Path(PROFILES[profile])
    settings = load_settings(path, profile=profile)
    radio, recording, dashboard = (settings[k] for k in ("radio", "recording", "dashboard"))
    db = Path(recording["database"])
    status = OperationStatus(status_output, station=recording["station"],
        samples_recorded=0, latest_telemetry=None) if status_output else None
    factory = lambda: create_connection(radio["address"], radio["transport"], radio["rfcomm_channel"] or None,
                                 connect_timeout=recording["connect_timeout"])
    with InstanceLock(db.with_name(db.name + ".app.lock")):
        factory()  # Validate optional dependency before starting any service.
        LOG.info("Settings: %s | Radio: %s | Database: %s", path.resolve(), radio["address"], db)
        store = Store(db, recording["station"], recording["role"], "benlink_bluetooth", settings)
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
                                    browser=dashboard["open_browser"] and not no_browser, status=status))
        except KeyboardInterrupt:
            store.event("stopped", {"reason": "keyboard_interrupt"})
        except Exception as exc:
            if status:
                status.update(state="failed", error=f"{type(exc).__name__}: {exc}", phase="ended")
            store.event("error", {"error": str(exc), "type": type(exc).__name__})
            raise
        finally:
            if server:
                server.server_close()
            store.close()
            if status and status.report["state"] != "failed":
                status.update(state="stopped", phase="ended")
