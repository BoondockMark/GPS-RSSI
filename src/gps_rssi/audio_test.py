"""Explicit bounded tone transmission and receive-only Bluetooth WAV capture."""

import asyncio
from datetime import timedelta
import hashlib
import json
import logging
from pathlib import Path
import tempfile
import uuid
import wave

from .audio_signal import RATE, SbcDecoder, encode_sbc, tone_pcm, validate_tone, write_wav
from .audio_transport import AudioParser, END_AUDIO, audio_packet, discover_services, mac_address, new_stream, select_audio_service
from .benlink_radio import create_connection
from .locking import InstanceLock
from .measurements import finite, utc_now, timestamp

LOG = logging.getLogger(__name__)


def write_report(output, report):
    temporary = output / ".report.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(output / "report.json")


def audio_lock(address):
    digest = hashlib.sha256(mac_address(address).encode()).hexdigest()[:20]
    return InstanceLock(Path(tempfile.gettempdir()) / ("gps-rssi-audio-" + digest + ".lock"),
                        message="Another audio test is already using this radio")


async def bounded_cleanup(operation):
    """Let bounded stop operations finish even if Ctrl-C/cancellation is repeated."""
    task = asyncio.create_task(operation)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def cleanup_audio(stream, control, *, stop=False, settle=1.5):
    errors, verified = [], False
    if stop:
        for _ in range(2):
            try:
                await asyncio.wait_for(stream.write(END_AUDIO), 1)
            except Exception as exc:
                errors.append(f"end_audio: {type(exc).__name__}: {exc}")
            await asyncio.sleep(.05)
        # The audio protocol has no stop acknowledgement; allow buffered bytes to drain.
        await asyncio.sleep(settle)
        for _ in range(3):
            try:
                status = await asyncio.wait_for(control.get_status(), 1)
                if not status.is_in_tx:
                    verified = True
                    break
            except Exception as exc:
                errors.append(f"verify_stop: {type(exc).__name__}: {exc}")
            await asyncio.sleep(.1)
    for name, operation in (("audio_disconnect", stream.close), ("control_disconnect", control.disconnect)):
        try:
            await asyncio.wait_for(operation(), 2)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return dict(stop_attempted=stop, stop_verified=verified if stop else None, cleanup_errors=errors)


async def stream_tone(stream, packets, *, write_timeout=.5):
    """Pace to the encoded sample clock; abort rather than catch up after a stall."""
    loop = asyncio.get_running_loop()
    start, sent = loop.time(), 0
    duration = sum(count for _, count in packets) / RATE
    async with asyncio.timeout(duration + .5):
        for payload, samples in packets:
            target = start + sent / RATE
            if loop.time() - target > .1:
                raise RuntimeError("Bluetooth audio pacing stalled; transmission aborted")
            await asyncio.sleep(max(0, target - loop.time()))
            await asyncio.wait_for(stream.write(audio_packet(payload)), write_timeout)
            sent += samples
        await asyncio.sleep(max(0, start + sent / RATE - loop.time()))


async def drain_audio(stream):
    while await stream.read():
        await asyncio.sleep(0)
    raise RuntimeError("Bluetooth audio disconnected during transmission")


async def transmit_tone(stream, control, address, service, packets, report, *, connect_timeout=30, settle=1.5):
    tasks = []
    stop = False
    try:
        await asyncio.wait_for(control.connect(), connect_timeout)
        status = await asyncio.wait_for(control.get_status(), 2)
        if status.is_in_tx:
            raise RuntimeError("Radio is already transmitting; tone test canceled")
        report["before_status"] = status.model_dump(mode="json")
        await asyncio.wait_for(stream.connect(address, service), connect_timeout)
        stop = True  # Every subsequent exit must attempt an end-of-audio message.
        report.update(state="transmitting", transmission_started_at=utc_now())
        LOG.warning("Sending test-tone audio to %s on audio channel %s", address, service["channel"])
        sender = asyncio.create_task(stream_tone(stream, packets))
        drain = asyncio.create_task(drain_audio(stream))
        tasks = [sender, drain]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if drain in done:
            await drain
        await sender
    finally:
        async def finish():
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return await cleanup_audio(stream, control, stop=stop, settle=settle)
        report.update(await bounded_cleanup(finish()))
        report["transmission_finished_at"] = utc_now()
        if stop and not report["stop_verified"]:
            # Report even when another exception/cancellation is already propagating.
            LOG.error("RADIO STOP NOT VERIFIED. Check the radio and stop transmission manually if necessary.")
    if not report["stop_verified"]:
        raise RuntimeError("Radio stop could not be verified; check the radio immediately")


def run_tone(output, *, frequency=1000, duration=3, level_dbfs=-18, transmit=False,
             address=None, audio_channel=None, connect_timeout=30):
    validate_tone(frequency, duration, level_dbfs)
    if not finite(connect_timeout) or not 1 <= connect_timeout <= 120:
        raise ValueError("Connection timeout must be between 1 and 120 seconds")
    if bool(address) != bool(transmit) or (audio_channel is not None and not transmit):
        raise ValueError("Use --transmit together with --address for RF; omit both to prepare files only")
    if address:
        address = mac_address(address)
    pcm = tone_pcm(frequency, duration, level_dbfs)
    packets = encode_sbc(pcm)  # Codec and format failures occur before any connection.
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # Refuse existing files/directories; keep results after interruption.
    report = dict(kind="tone", id=str(uuid.uuid4()), state="prepared", created_at=utc_now(), frequency_hz=frequency,
                  duration_seconds=duration, level_dbfs_peak=level_dbfs, sample_rate=RATE,
                  sbc=dict(bitpool=16, blocks=16, subbands=8, channels=1), address=address,
                  transmission_requested=transmit, stop_attempted=False, stop_verified=None)
    try:
        write_wav(output / "tone.wav", pcm)
        (output / "tone.sbc").write_bytes(b"".join(p for p, _ in packets))
        if transmit:
            with audio_lock(address):
                async def run():
                    services = await discover_services(address)
                    service = select_audio_service(services, audio_channel)
                    report["audio_service"] = service
                    write_report(output, report)
                    control = create_connection(address, "ble", None, connect_timeout=connect_timeout)
                    await transmit_tone(new_stream(), control, address, service, packets, report,
                                        connect_timeout=connect_timeout)
                LOG.info("Requested %.1fs RF tone at the radio's current channel and power", duration)
                asyncio.run(run())
                report["state"] = "completed"
    except (KeyboardInterrupt, asyncio.CancelledError):
        report.update(state="cancelled", error="Interrupted")
        raise
    except Exception as exc:
        report.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["finished_at"] = utc_now()
        write_report(output, report)
    return report


async def receive_audio(stream, control, address, service, wav, report, *, duration=30, connect_timeout=30):
    parser, decoder = AudioParser(), SbcDecoder()
    segments = report["segments"]
    cursor, active, last_arrival = 0, None, None
    tasks = []

    def append(blocks, received_at):
        nonlocal cursor, active
        for block in blocks:
            if active is None:
                active = dict(start_sample=cursor, end_sample=cursor,
                    estimated_started_at=(timestamp(received_at) - timedelta(seconds=len(block) / RATE)).isoformat(),
                    first_received_at=received_at, closed_by=None)
                segments.append(active)
            wav.writeframesraw(block.tobytes())
            cursor += len(block)
            active["end_sample"] = cursor
        report["decoded_samples"] = cursor

    def boundary(reason, received_at):
        nonlocal active, decoder
        append(decoder.decode(b""), received_at)
        if active is not None:
            active["closed_by"] = reason
        active, decoder = None, SbcDecoder()

    async def read():
        nonlocal last_arrival
        while True:
            chunk = await stream.read()
            if not chunk:
                raise RuntimeError("Bluetooth audio disconnected; capture is incomplete")
            now = utc_now()
            for kind, payload in parser.feed(chunk):
                if kind in (0, 3):  # Both observed RX variants; 0x09 is local TX echo.
                    current = asyncio.get_running_loop().time()
                    if last_arrival is not None and current - last_arrival > .25:
                        boundary("host_delivery_gap", now)
                    last_arrival = current
                    append(decoder.decode(payload), now)
                    report["received_packets"] += 1
                elif kind == 1:
                    boundary("audio_end", now)
                    last_arrival = None
                elif kind == 9:
                    raise RuntimeError("Local transmit echo received; capture is not receive-only")
                elif kind != 2:
                    raise ValueError(f"Unknown audio frame type: {kind}")
            await asyncio.sleep(0)

    async def monitor():
        while True:
            status = await asyncio.wait_for(control.get_status(), 2)
            if status.is_in_tx:
                raise RuntimeError("Capture radio entered transmit; receive-only capture aborted")
            await asyncio.sleep(.5)

    try:
        await asyncio.wait_for(control.connect(), connect_timeout)
        status = await asyncio.wait_for(control.get_status(), 2)
        if status.is_in_tx:
            raise RuntimeError("Capture radio is transmitting")
        await asyncio.wait_for(stream.connect(address, service), connect_timeout)
        report.update(state="capturing", capture_started_at=utc_now())
        tasks = [asyncio.create_task(read()), asyncio.create_task(monitor())]
        done, _ = await asyncio.wait(tasks, timeout=duration, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    finally:
        async def finish():
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                boundary("capture_end", utc_now())
            finally:
                report.update(await cleanup_audio(stream, control))
        await bounded_cleanup(finish())
        report["capture_finished_at"] = utc_now()


def run_receive(output, *, address, audio_channel=None, duration=30, station="receiver", connect_timeout=30):
    if not finite(duration) or not 1 <= duration <= 600:
        raise ValueError("Capture duration must be between 1 and 600 seconds")
    if not finite(connect_timeout) or not 1 <= connect_timeout <= 120:
        raise ValueError("Connection timeout must be between 1 and 120 seconds")
    address = mac_address(address)
    SbcDecoder()  # Fail on missing optional dependencies before any connections/files.
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    report = dict(kind="audio_capture", id=str(uuid.uuid4()), state="prepared", created_at=utc_now(), address=address,
                  station=station, sample_rate=RATE, duration_seconds=duration, decoded_samples=0,
                  received_packets=0, segments=[], time_reference="host receipt estimate; Bluetooth buffering and host scheduling add uncertainty")
    write_report(output, report)
    try:
        with audio_lock(address), (output / "audio.wav").open("xb") as stream, wave.open(stream, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            async def run():
                service = select_audio_service(await discover_services(address), audio_channel)
                report["audio_service"] = service
                control = create_connection(address, "ble", None, connect_timeout=connect_timeout)
                await receive_audio(new_stream(), control, address, service, wav, report,
                                    duration=duration, connect_timeout=connect_timeout)
            asyncio.run(run())
        report["state"] = "completed"
    except (KeyboardInterrupt, asyncio.CancelledError):
        report.update(state="cancelled", error="Interrupted")
        raise
    except Exception as exc:
        report.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["finished_at"] = utc_now()
        audio = output / "audio.wav"
        if audio.exists():
            report["wav_sha256"] = hashlib.sha256(audio.read_bytes()).hexdigest()
        write_report(output, report)
    return report
