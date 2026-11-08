import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gps_rssi.automatic import automatic_tones, clock_reference, first_tone
from gps_rssi.audio_transport import END_AUDIO
from gps_rssi.audio_test import run_tone
from gps_rssi.cli import parser

NOW = datetime(2026, 11, 8, 12, 59, 55, tzinfo=timezone.utc)


def resources():
    async def read():
        await asyncio.Event().wait()
    stream = Mock(connect=AsyncMock(), close=AsyncMock(), write=AsyncMock(), read=read)
    control = Mock(connect=AsyncMock(), disconnect=AsyncMock(),
                   get_status=AsyncMock(return_value=SimpleNamespace(is_in_tx=False, is_gps_locked=False)))
    return stream, control


class StartTests(unittest.TestCase):
    def test_minute_rollover_and_mobile_offset(self):
        fixed, mobile = first_tone(NOW, "fixed"), first_tone(NOW, "mobile")
        self.assertEqual(fixed.isoformat(), "2026-11-08T13:00:00+00:00")
        self.assertEqual((mobile - fixed).total_seconds(), 10)
        self.assertEqual(first_tone(fixed, "fixed"), fixed + timedelta(minutes=1))
        with self.assertRaises(ValueError):
            first_tone(NOW, "other")

    def test_automatic_requires_explicit_transmit_and_role(self):
        for options in (dict(automatic="fixed"), dict(automatic="other", transmit=True)):
            with self.assertRaises(ValueError), patch("gps_rssi.audio_test.discover_services") as discover:
                run_tone("unused", **options)
            discover.assert_not_called()
        args = parser().parse_args(["tone", "--output", "unused", "--transmit", "--address", "AA:BB:CC:DD:EE:FF", "--automatic", "mobile"])
        self.assertEqual(args.automatic, "mobile")


class CycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_gps_clock_is_preferred_even_when_host_clock_disagrees(self):
        _, control = resources()
        control.get_status.return_value.is_gps_locked = True
        control.get_position = AsyncMock(side_effect=[SimpleNamespace(time=NOW), SimpleNamespace(time=NOW + timedelta(seconds=1))])
        with patch("gps_rssi.automatic.asyncio.sleep", AsyncMock()):
            now, _, source, reason = await clock_reference(control)
        self.assertEqual(now, NOW + timedelta(seconds=1))
        self.assertEqual(source, "radio GPS")
        self.assertIsNone(reason)

    async def test_missing_stale_invalid_or_failed_gps_falls_back(self):
        for mode in ("unlocked", "stale", "naive", "failure"):
            _, control = resources()
            control.get_status.return_value.is_gps_locked = mode != "unlocked"
            times = [NOW, NOW if mode == "stale" else NOW + timedelta(seconds=1)]
            if mode == "naive":
                times = [t.replace(tzinfo=None) for t in times]
            control.get_position = AsyncMock(side_effect=TimeoutError() if mode == "failure" else [SimpleNamespace(time=t) for t in times])
            with patch("gps_rssi.automatic.asyncio.sleep", AsyncMock()):
                now, _, source, reason = await clock_reference(control)
            self.assertIsNotNone(now.utcoffset())
            self.assertEqual(source, "computer")
            self.assertTrue(reason)

    async def test_cadence_does_not_accumulate_cleanup_delay(self):
        stream, control = resources()
        elapsed, starts = [0.0], []
        real_sleep = asyncio.sleep
        async def sleep(seconds):
            elapsed[0] += seconds
            await real_sleep(0)
        async def send(*args):
            starts.append(elapsed[0])
            elapsed[0] += 5
            if len(starts) == 3:
                raise RuntimeError("finish test")
        report = dict(tones_sent=0)
        with patch("gps_rssi.automatic.asyncio.get_running_loop", return_value=SimpleNamespace(time=lambda: elapsed[0])), \
             patch("gps_rssi.automatic.asyncio.sleep", sleep), \
             patch("gps_rssi.automatic.clock_reference", AsyncMock(return_value=(NOW, 0, "radio GPS", None))), \
             patch("gps_rssi.automatic.stream_tone", send):
            with self.assertRaisesRegex(RuntimeError, "finish test"):
                await automatic_tones(stream, control, "address", {}, [], report, role="fixed", settle=1.5)
        self.assertEqual(starts, [5, 25, 45])
        self.assertEqual(report["tones_sent"], 2)
        self.assertTrue(report["stop_verified"])
        self.assertTrue(all(c.args[0] == END_AUDIO for c in stream.write.await_args_list))
        stream.close.assert_awaited_once()
        control.disconnect.assert_awaited_once()

    async def cancel_at(self, during_tone):
        stream, control = resources()
        reached = asyncio.Event()
        report = dict(tones_sent=0)
        async def send(*args):
            reached.set()
            await asyncio.Event().wait()
        def update():
            if report.get("state") == "waiting" and not during_tone:
                reached.set()
        now = datetime.now(timezone.utc)
        with patch("gps_rssi.automatic.clock_reference", AsyncMock(return_value=(now, asyncio.get_running_loop().time(), "computer", "no GPS"))), \
             patch("gps_rssi.automatic.first_tone", return_value=now if during_tone else now + timedelta(seconds=60)), \
             patch("gps_rssi.automatic.stream_tone", send):
            task = asyncio.create_task(automatic_tones(stream, control, "address", {}, [], report, role="mobile", settle=0, on_update=update))
            await asyncio.wait_for(reached.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        stream.close.assert_awaited_once()
        control.disconnect.assert_awaited_once()
        if during_tone:
            self.assertTrue(report["stop_verified"])
            self.assertEqual(stream.write.await_count, 2)
        else:
            stream.write.assert_not_awaited()

    async def test_halt_while_armed_sends_no_tone(self):
        await self.cancel_at(False)

    async def test_halt_during_tone_dekeys_and_verifies(self):
        await self.cancel_at(True)

    async def test_missed_deadline_halts_without_sending_a_late_tone(self):
        stream, control = resources()
        with patch("gps_rssi.automatic.clock_reference", AsyncMock(return_value=(NOW, asyncio.get_running_loop().time() - 120, "computer", None))):
            with self.assertRaisesRegex(RuntimeError, "deadline missed"):
                await automatic_tones(stream, control, "address", {}, [], dict(tones_sent=0), role="fixed", settle=0)
        stream.write.assert_not_awaited()
        stream.close.assert_awaited_once()

    async def test_unverified_stop_halts_without_a_second_tone(self):
        stream, control = resources()
        idle = SimpleNamespace(is_in_tx=False)
        tx = SimpleNamespace(is_in_tx=True)
        control.get_status.side_effect = [idle, idle, tx, tx, tx, tx]
        report = dict(tones_sent=0)
        now = datetime.now(timezone.utc)
        with patch("gps_rssi.automatic.clock_reference", AsyncMock(return_value=(now, asyncio.get_running_loop().time(), "computer", None))), \
             patch("gps_rssi.automatic.first_tone", return_value=now), \
             patch("gps_rssi.automatic.stream_tone", AsyncMock()) as send:
            with self.assertRaisesRegex(RuntimeError, "stop could not be verified"):
                await automatic_tones(stream, control, "address", {}, [], report, role="fixed", settle=0)
        send.assert_awaited_once()
        self.assertFalse(report["stop_verified"])
        self.assertTrue(report["stop_attempted"])

    async def test_audio_disconnect_halts_while_armed(self):
        stream, control = resources()
        stream.read = AsyncMock(return_value=b"")
        now = datetime.now(timezone.utc)
        with patch("gps_rssi.automatic.clock_reference", AsyncMock(return_value=(now, asyncio.get_running_loop().time(), "computer", None))):
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                await automatic_tones(stream, control, "address", {}, [], dict(tones_sent=0), role="fixed", settle=0)
        stream.write.assert_not_awaited()
        stream.close.assert_awaited_once()

    async def test_halt_during_receive_gap_prevents_next_tone(self):
        stream, control = resources()
        report = dict(tones_sent=0)
        reached = asyncio.Event()
        def update():
            if report.get("state") == "receiving":
                reached.set()
        now = datetime.now(timezone.utc)
        with patch("gps_rssi.automatic.clock_reference", AsyncMock(return_value=(now, asyncio.get_running_loop().time(), "computer", None))), \
             patch("gps_rssi.automatic.first_tone", return_value=now), \
             patch("gps_rssi.automatic.stream_tone", AsyncMock()) as send:
            task = asyncio.create_task(automatic_tones(stream, control, "address", {}, [], report, role="fixed", settle=0, on_update=update))
            await asyncio.wait_for(reached.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        send.assert_awaited_once()
        self.assertTrue(report["stop_verified"])

    async def test_already_transmitting_is_never_interrupted(self):
        stream, control = resources()
        control.get_status.return_value.is_in_tx = True
        with self.assertRaisesRegex(RuntimeError, "already transmitting"):
            await automatic_tones(stream, control, "address", {}, [], {}, role="fixed", settle=0)
        stream.write.assert_not_awaited()
        stream.connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
