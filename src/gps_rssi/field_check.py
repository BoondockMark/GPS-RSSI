"""Read-only field preflight: prove connections without sending audio or PTT."""

import asyncio
from contextlib import closing
import hashlib
from pathlib import Path
import tempfile

from .audio_session import bounded_cleanup, cleanup_audio
from .audio_signal import encode_sbc, tone_pcm
from .audio_test import audio_lock, write_report
from .audio_transport import discover_services, new_stream, select_audio_service
from .automatic import clock_reference
from .benlink_radio import create_connection
from .field_recording import recording_settings
from .locking import InstanceLock
from .measurements import utc_now
from .sharing import check_log, readonly


async def inspect_radio(control, stream, address, report, *, timeout=30):
    checks = report["checks"]
    try:
        await asyncio.wait_for(control.connect(), timeout)
        status = await asyncio.wait_for(control.get_status(), 2)
        if status.is_in_tx:
            raise RuntimeError("Radio is transmitting. Release PTT before checking again.")
        channel = await asyncio.wait_for(control.get_channel(status.curr_ch_id), 2)
        report["channel"] = channel.model_dump(mode="json")
        checks.append(dict(name="Radio control", state="ready", detail="Connected; TX is off. Channel settings read."))
        service = select_audio_service(await discover_services(address), None)
        await asyncio.wait_for(stream.connect(address, service), timeout)
        checks.append(dict(name="Audio connection", state="ready", detail="Connected without transmitting."))
        now, _, source, fallback = await clock_reference(control)
        report.update(clock_source=source, clock_fallback=fallback, clock_at=now.isoformat())
        checks.append(dict(name="Timing", state="attention" if fallback else "ready",
            detail=f"Using {source}. " + (fallback + ". Synchronize this computer's clock." if fallback else "GPS clock is advancing.")))
        report["ready"] = True
    except Exception as exc:
        checks.append(dict(name="Radio connection", state="unavailable",
            detail=f"{type(exc).__name__}: {exc}. Check pairing, radio power, and other radio apps; then check again."))
    finally:
        report.update(await bounded_cleanup(cleanup_audio(stream, control)))
        if report["cleanup_errors"]:
            report["ready"] = False
            checks.append(dict(name="Connection cleanup", state="unavailable", detail="Connection did not close cleanly. Check the radio before continuing."))


def run_check(output, config):
    config = Path(config).resolve()
    settings = recording_settings(config)
    address = settings["radio"]["address"]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(kind="field_check", state="checking", ready=False, checks=[],
        profile_revision=hashlib.sha256(config.read_bytes()).hexdigest(), checked_at=utc_now())
    write_report(output, report)
    try:
        encode_sbc(tone_pcm(duration=.5))
        report["checks"].append(dict(name="Audio tools", state="ready", detail="Tone encoder is available."))
        database = Path(settings["recording"]["database"])
        database.parent.mkdir(parents=True, exist_ok=True)
        with InstanceLock(database.with_name(database.name + ".app.lock")):
            if database.exists():
                with database.open("r+b"):
                    pass  # Test access without changing the recording.
                with closing(readonly(database)) as existing:
                    check_log(existing)
            with tempfile.TemporaryFile(dir=database.parent) as probe:
                probe.write(b"field-check")
                probe.flush()
            report["checks"].append(dict(name="Recording destination", state="ready", detail="Writable; no other managed recorder owns this file."))
            with audio_lock(address):
                control = create_connection(address, "ble", None, connect_timeout=30)
                asyncio.run(inspect_radio(control, new_stream(), address, report))
    except (KeyboardInterrupt, asyncio.CancelledError):
        report.update(state="cancelled", ready=False)
        raise
    except Exception as exc:
        report["ready"] = False
        report["checks"].append(dict(name="Preparation", state="unavailable", detail=f"{type(exc).__name__}: {exc}"))
    finally:
        if report["state"] != "cancelled":
            report["state"] = "completed"
        report["checked_at"] = utc_now()
        write_report(output, report)
    return report
