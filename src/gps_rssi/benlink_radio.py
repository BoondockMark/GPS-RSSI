"""Read-only acquisition through Benlink's command API.

Only GET_DEVICE_INFO, GET_HT_STATUS, READ_RF_CH, and GET_POSITION are used. No radio
configuration, PTT, audio, or packet transmissions are requested.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .measurements import channel_frequencies, utc_now

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


def create_connection(address, transport, channel, connect_timeout=30):
    try:
        from benlink.command import CommandConnection
    except ImportError as exc:
        raise RuntimeError('Bluetooth support requires: pip install -e ".[bluetooth]"') from exc
    if not callable(getattr(CommandConnection, "get_position", None)):
        raise RuntimeError('Installed Benlink lacks the required GPS API. Reinstall the pinned dependency: '
                           'pip install --force-reinstall -e ".[bluetooth]"')
    if transport == "ble":
        from .benlink_ble import RecoveringBleCommandLink
        return CommandConnection(RecoveringBleCommandLink(address, timeout=connect_timeout))
    return CommandConnection.new_rfcomm(address, channel=channel)


async def collect(store, factory, *, interval=1, timeout=10, gps=False,
                  reconnect_delay=5, max_samples=None, connect_timeout=30, on_status=None):
    count = 0
    attempt = 0
    while max_samples is None or count < max_samples:
        attempt += 1
        conn = factory()
        try:
            if on_status:
                on_status(state="connecting", attempt=attempt, phase="connect", retry_at=None,
                          valid_for=connect_timeout + timeout + 5)
            LOG.info("Connecting (attempt %d; timeout %.1fs)", attempt, connect_timeout)
            await request(conn.connect(), connect_timeout, "connect")
            LOG.info("Bluetooth connected; reading device information")
            info = await request(conn.get_device_info(), timeout, "device_info")
            store.event("connected", info.model_dump(mode="json"))
            if on_status:
                on_status(state="connected", phase="sample", valid_for=3 * timeout + interval + 5)
            LOG.info("Connected; device info recorded in SQLite")
            while max_samples is None or count < max_samples:
                started = asyncio.get_running_loop().time()
                requested_at = utc_now()
                status = await request(conn.get_status(), timeout, "status")
                frame = {"status": status.model_dump(mode="json"), "position": None,
                         "status_requested_at": requested_at, "status_received_at": utc_now()}
                frequency_error = None
                try:
                    # Refresh every sample: settings can change without a channel-ID change.
                    channel = await request(conn.get_channel(status.curr_ch_id), timeout, "channel")
                    channel_data = channel.model_dump(mode="json")
                    channel_frequencies({**frame, "channel": channel_data})
                    frame["channel"] = channel_data
                except Exception as exc:
                    frequency_error = exc
                    store.event("frequency_error", {"error": str(exc), "type": type(exc).__name__})
                gps_error = None
                if gps and status.is_gps_locked and frequency_error is None:
                    try:
                        pos = await request(conn.get_position(), timeout, "position")
                        frame["position"] = pos.model_dump(mode="json")
                    except Exception as exc:
                        gps_error = exc
                        store.event("gps_error", {"error": str(exc), "type": type(exc).__name__})
                store.sample(frame)
                count += 1
                if on_status:
                    on_status(state="recording", phase="sample", samples_recorded=count,
                        error=None, error_stage=None, cleanup_error=None, retry_at=None,
                        latest_telemetry=dict(received_at=frame["status_received_at"], rssi=status.rssi,
                            rssi_unit="benshi_relative_0_100", is_in_rx=status.is_in_rx,
                            is_in_tx=status.is_in_tx, gps_locked=status.is_gps_locked,
                            gps_recorded=frame["position"] is not None), valid_for=3 * timeout + interval + 5)
                LOG.info("Sample %d: relative RSSI %.1f, RX=%s TX=%s GPS=%s",
                         count, status.rssi, status.is_in_rx, status.is_in_tx,
                         frame["position"] is not None)
                if gps_error is not None or frequency_error is not None:
                    # A timed-out Benlink request may retain a reply handler.
                    # Use a fresh connection rather than reuse that request state.
                    raise gps_error or frequency_error
                if max_samples is None or count < max_samples:
                    await asyncio.sleep(max(0, interval - (asyncio.get_running_loop().time() - started)))
        except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
            if on_status:
                on_status(state="reconnecting", error=str(exc), error_stage=getattr(exc, "stage", "processing"),
                          phase="disconnect", valid_for=timeout + reconnect_delay + 5)
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
                if on_status:
                    on_status(cleanup_error=str(exc), valid_for=reconnect_delay + 5)
                store.event("disconnect_error", {"error": str(exc), "type": type(exc).__name__,
                                                 "attempt": attempt})
                LOG.warning("Disconnect cleanup failed (%s): %s; Bluetooth may still be busy",
                            type(exc).__name__, exc)
        if max_samples is None or count < max_samples:
            if on_status:
                on_status(state="reconnecting", phase="retry",
                    retry_at=(datetime.now(timezone.utc) + timedelta(seconds=reconnect_delay)).isoformat(),
                    valid_for=reconnect_delay + 5)
            LOG.info("Reconnecting in %.1f seconds", reconnect_delay)
            await asyncio.sleep(reconnect_delay)
    return count
