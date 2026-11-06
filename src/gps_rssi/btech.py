"""Read-only acquisition through Benlink's command API.

Only GET_DEVICE_INFO, GET_HT_STATUS, and GET_POSITION are used. No radio
configuration, PTT, audio, or packet transmissions are requested.
"""

import asyncio
import logging

from .storage import utc_now

LOG = logging.getLogger(__name__)


class RadioRequestError(RuntimeError):
    def __init__(self, stage, timeout, cause):
        self.stage = stage
        self.error_type = type(cause).__name__
        detail = f"timed out after {timeout:g}s" if isinstance(cause, TimeoutError) else str(cause)
        super().__init__(f"{stage}: {self.error_type}: {detail}")


async def request(operation, timeout, stage):
    """Normalize backend exceptions without catching cancellation or DB errors."""
    try:
        return await asyncio.wait_for(operation, timeout)
    except Exception as exc:
        raise RadioRequestError(stage, timeout, exc) from exc


def connection(address, transport, channel, connect_timeout=30):
    try:
        from benlink.command import CommandConnection
    except ImportError as exc:
        raise RuntimeError('Bluetooth support requires: pip install -e ".[bluetooth]"') from exc
    if not callable(getattr(CommandConnection, "get_position", None)):
        raise RuntimeError('Installed Benlink lacks the required GPS API. Reinstall the pinned dependency: '
                           'pip install --force-reinstall -e ".[bluetooth]"')
    if transport == "ble":
        from .ble import RecoveringBleCommandLink
        return CommandConnection(RecoveringBleCommandLink(address, timeout=connect_timeout))
    return CommandConnection.new_rfcomm(address, channel=channel)


async def collect(store, factory, *, interval=1, timeout=10, gps=False,
                  reconnect_delay=5, max_samples=None, connect_timeout=30):
    count = 0
    attempt = 0
    while max_samples is None or count < max_samples:
        attempt += 1
        conn = factory()
        try:
            LOG.info("Connecting (attempt %d; timeout %.1fs)", attempt, connect_timeout)
            await request(conn.connect(), connect_timeout, "connect")
            LOG.info("Bluetooth connected; reading device information")
            info = await request(conn.get_device_info(), timeout, "device_info")
            store.event("connected", info.model_dump(mode="json"))
            LOG.info("Connected; device info recorded in SQLite")
            while max_samples is None or count < max_samples:
                started = asyncio.get_running_loop().time()
                requested_at = utc_now()
                status = await request(conn.get_status(), timeout, "status")
                frame = {"status": status.model_dump(mode="json"), "position": None,
                         "status_requested_at": requested_at, "status_received_at": utc_now()}
                gps_error = None
                if gps and status.is_gps_locked:
                    try:
                        pos = await request(conn.get_position(), timeout, "position")
                        frame["position"] = pos.model_dump(mode="json")
                    except Exception as exc:
                        gps_error = exc
                        store.event("gps_error", {"error": str(exc), "type": type(exc).__name__})
                store.sample(frame)
                count += 1
                LOG.info("Sample %d: relative RSSI %.1f, RX=%s TX=%s GPS=%s",
                         count, status.rssi, status.is_in_rx, status.is_in_tx,
                         frame["position"] is not None)
                if gps_error is not None:
                    # A timed-out Benlink request may retain a reply handler.
                    # Use a fresh connection rather than reuse that request state.
                    raise gps_error
                if max_samples is None or count < max_samples:
                    await asyncio.sleep(max(0, interval - (asyncio.get_running_loop().time() - started)))
        except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
            store.event("connection_error", {"error": str(exc), "type": type(exc).__name__,
                                            "stage": getattr(exc, "stage", "processing"),
                                            "cause_type": getattr(exc, "error_type", type(exc).__name__),
                                            "attempt": attempt})
            LOG.warning("Connection failed (%s): %s", type(exc).__name__, exc)
        finally:
            try:
                await request(conn.disconnect(), timeout, "disconnect")
                store.event("disconnected", {"attempt": attempt})
            except Exception as exc:
                store.event("disconnect_error", {"error": str(exc), "type": type(exc).__name__,
                                                 "attempt": attempt})
                LOG.warning("Disconnect cleanup failed (%s): %s; Bluetooth may still be busy",
                            type(exc).__name__, exc)
        if max_samples is None or count < max_samples:
            LOG.info("Reconnecting in %.1f seconds", reconnect_delay)
            await asyncio.sleep(reconnect_delay)
    return count
