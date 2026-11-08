"""Minute-aligned automatic tone cycles using a monotonic timing anchor."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging

from .audio_session import bounded_cleanup, cleanup_audio, drain_audio, stream_tone
from .audio_transport import END_AUDIO
from .measurements import utc_now

LOG = logging.getLogger(__name__)


def first_tone(now, role):
    """Arm for the next minute, then apply the mobile station's ten-second offset."""
    if role not in ("fixed", "mobile"):
        raise ValueError("Choose fixed or mobile automatic mode")
    return now.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=10 if role == "mobile" else 0)


async def clock_reference(control):
    """Prefer an advancing GPS timestamp; a cached fix is not a running clock.

    Compare successive GPS readings with elapsed monotonic time, so a wrong host
    clock does not disqualify GPS. Never change the anchor during a running cycle.
    """
    loop = asyncio.get_running_loop()
    try:
        status = await asyncio.wait_for(control.get_status(), 2)
        if status.is_gps_locked:
            first = await asyncio.wait_for(control.get_position(), 2)
            start = loop.time()
            await asyncio.sleep(1.1)
            second = await asyncio.wait_for(control.get_position(), 2)
            end = loop.time()
            advanced = (second.time - first.time).total_seconds()
            if (second.time.utcoffset() is not None and advanced > 0
                    and abs(advanced - (end - start)) <= 1.5):
                return second.time, end, "radio GPS", None
            reason = "GPS timestamp is invalid or not advancing"
        else:
            reason = "Radio GPS is not locked"
    except Exception as exc:
        reason = f"GPS clock unavailable: {type(exc).__name__}: {exc}"
    return datetime.now(timezone.utc), loop.time(), "computer", reason


async def automatic_tones(stream, control, address, service, packets, report, *, role,
                          connect_timeout=30, settle=1.5, on_update=None, recorder=None):
    loop = asyncio.get_running_loop()
    tasks, stop = [], False

    def update(**values):
        report.update(values, reported_at=utc_now())
        if on_update:
            on_update()

    async def cycle():
        nonlocal stop
        now, anchor, source, fallback = await clock_reference(control)
        first = first_tone(now, role)
        target = anchor + (first - now).total_seconds()
        update(state="waiting", clock_source=source, clock_fallback=fallback,
               first_tone_at=first.isoformat(), next_tone_at=first.isoformat(),
               next_tone_host_at=(datetime.now(timezone.utc) + timedelta(seconds=target - loop.time())).isoformat())
        while True:
            # Query in the guard interval, never on the tone's start deadline.
            if recorder:
                # Leave three seconds for the final TX guard check. All control
                # requests are serial; none compete with tone start or cleanup.
                while target - loop.time() > 5:
                    started = loop.time()
                    await recorder.sample(control)
                    update()
                    await asyncio.sleep(max(0, 1 - (loop.time() - started)))
            await asyncio.sleep(max(0, target - loop.time() - 3))
            status = await asyncio.wait_for(control.get_status(), 2)
            if status.is_in_tx:
                raise RuntimeError("Radio is already transmitting; automatic mode halted")
            await asyncio.sleep(max(0, target - loop.time()))
            if loop.time() - target > .1:
                raise RuntimeError("Automatic tone deadline missed; mode halted")
            stop = True
            update(state="transmitting", stop_attempted=True, stop_verified=False,
                   transmission_started_at=utc_now())
            await stream_tone(stream, packets)
            await asyncio.wait_for(stream.write(END_AUDIO), 1)
            await asyncio.sleep(settle)
            status = await asyncio.wait_for(control.get_status(), 2)
            if status.is_in_tx:
                raise RuntimeError("Radio stop could not be verified; automatic mode halted")
            report["tones_sent"] += 1
            target += 20
            update(state="receiving", stop_verified=True, transmission_finished_at=utc_now(),
                   next_tone_at=(first + timedelta(seconds=20 * report["tones_sent"])).isoformat(),
                   next_tone_host_at=(datetime.now(timezone.utc) + timedelta(seconds=target - loop.time())).isoformat())

    try:
        await asyncio.wait_for(control.connect(), connect_timeout)
        status = await asyncio.wait_for(control.get_status(), 2)
        if status.is_in_tx:
            raise RuntimeError("Radio is already transmitting; automatic mode canceled")
        await asyncio.wait_for(stream.connect(address, service), connect_timeout)
        update(preflight_passed=True)
        tasks = [asyncio.create_task(cycle()), asyncio.create_task(drain_audio(stream))]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    finally:
        async def finish():
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return await cleanup_audio(stream, control, stop=stop, settle=settle)
        report.update(await bounded_cleanup(finish()))
        if stop and not report["stop_verified"]:
            LOG.error("RADIO STOP NOT VERIFIED. Check the radio and stop transmission manually if necessary.")
        if on_update:
            on_update()
