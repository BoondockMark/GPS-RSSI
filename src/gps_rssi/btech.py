"""Read-only acquisition through Benlink's command API.

Only GET_DEVICE_INFO, GET_HT_STATUS, and GET_POSITION are used. No radio
configuration, PTT, audio, or packet transmissions are requested.
"""

import asyncio
import logging

from .storage import utc_now

LOG = logging.getLogger(__name__)


async def request(operation, timeout):
    """Normalize backend exceptions without catching cancellation or DB errors."""
    try:
        return await asyncio.wait_for(operation, timeout)
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


def connection(address, transport, channel):
    try:
        from benlink.command import CommandConnection
    except ImportError as exc:
        raise RuntimeError('Bluetooth support requires: pip install -e ".[bluetooth]"') from exc
    if not callable(getattr(CommandConnection, "get_position", None)):
        raise RuntimeError('Installed Benlink lacks the required GPS API. Reinstall the pinned dependency: '
                           'pip install --force-reinstall -e ".[bluetooth]"')
    if transport == "ble":
        return CommandConnection.new_ble(address)
    return CommandConnection.new_rfcomm(address, channel=channel)


async def collect(store, factory, *, interval=1, timeout=10, gps=False,
                  reconnect_delay=5, max_samples=None):
    count = 0
    while max_samples is None or count < max_samples:
        conn = factory()
        try:
            await request(conn.connect(), timeout)
            info = await request(conn.get_device_info(), timeout)
            store.event("connected", info.model_dump(mode="json"))
            LOG.info("Connected; device info recorded in SQLite")
            while max_samples is None or count < max_samples:
                started = asyncio.get_running_loop().time()
                requested_at = utc_now()
                status = await request(conn.get_status(), timeout)
                frame = {"status": status.model_dump(mode="json"), "position": None,
                         "status_requested_at": requested_at, "status_received_at": utc_now()}
                gps_error = None
                if gps and status.is_gps_locked:
                    try:
                        pos = await request(conn.get_position(), timeout)
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
            store.event("connection_error", {"error": str(exc), "type": type(exc).__name__})
            LOG.warning("Connection failed (%s): %s", type(exc).__name__, exc)
        finally:
            try:
                await asyncio.wait_for(conn.disconnect(), timeout)
            except Exception as exc:
                LOG.debug("Disconnect cleanup: %s", exc)
        if max_samples is None or count < max_samples:
            LOG.info("Reconnecting in %.1f seconds", reconnect_delay)
            await asyncio.sleep(reconnect_delay)
    return count
