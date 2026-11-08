"""Shared bounded audio streaming and radio-stop lifecycle."""

import asyncio

from .audio_signal import RATE
from .audio_transport import END_AUDIO, audio_packet


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
    operations = [("control_disconnect", control.disconnect)]
    if stream is not None:
        operations.insert(0, ("audio_disconnect", stream.close))
    for name, operation in operations:
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


