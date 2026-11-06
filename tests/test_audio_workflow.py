import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import wave

from gps_rssi.audio_test import cleanup_audio, receive_audio, stream_tone, transmit_tone
from gps_rssi.audio_transport import END_AUDIO, GENERIC_AUDIO, audio_packet
from gps_rssi.cli import parser, run

HAS_AUDIO = all(importlib.util.find_spec(name) for name in ("numpy", "scipy", "av"))
ADDRESS = "AA:BB:CC:DD:EE:FF"
SERVICE = dict(channel=2, service_uuids=[GENERIC_AUDIO])


def status(tx=False):
    return SimpleNamespace(is_in_tx=tx, model_dump=lambda **kwargs: dict(is_in_tx=tx))


def resources():
    stream = Mock(connect=AsyncMock(), close=AsyncMock(), write=AsyncMock(), read=AsyncMock())
    async def blocked_read():
        await asyncio.Event().wait()
    stream.read.side_effect = blocked_read
    control = Mock(connect=AsyncMock(), disconnect=AsyncMock(), get_status=AsyncMock(return_value=status()))
    return stream, control


class TransmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_sends_explicit_end_twice_and_verifies_radio_state(self):
        stream, control = resources()
        report = {}
        with patch("gps_rssi.audio_test.stream_tone", AsyncMock()):
            await transmit_tone(stream, control, ADDRESS, SERVICE, [], report, settle=0)
        self.assertEqual([c.args[0] for c in stream.write.await_args_list], [END_AUDIO, END_AUDIO])
        self.assertTrue(report["stop_verified"])
        control.disconnect.assert_awaited_once()
        stream.close.assert_awaited_once()

    async def test_failed_send_still_stops_disconnects_and_preserves_failure(self):
        stream, control = resources()
        report = {}
        with patch("gps_rssi.audio_test.stream_tone", AsyncMock(side_effect=TimeoutError("write stalled"))):
            with self.assertRaisesRegex(TimeoutError, "write stalled"):
                await transmit_tone(stream, control, ADDRESS, SERVICE, [], report, settle=0)
        self.assertTrue(report["stop_attempted"])
        self.assertTrue(report["stop_verified"])
        self.assertEqual(stream.write.await_count, 2)
        stream.close.assert_awaited_once()

    async def test_repeated_cancellation_does_not_skip_stop_cleanup(self):
        stream, control = resources()
        started = asyncio.Event()
        async def sender(*args):
            started.set()
            await asyncio.Event().wait()
        report = {}
        with patch("gps_rssi.audio_test.stream_tone", sender):
            task = asyncio.create_task(transmit_tone(stream, control, ADDRESS, SERVICE, [], report, settle=.05))
            await started.wait()
            task.cancel()
            await asyncio.sleep(.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(report["stop_verified"])
        self.assertEqual(stream.write.await_count, 2)
        control.disconnect.assert_awaited_once()

    async def test_connect_failure_closes_partial_resources_without_audio_writes(self):
        stream, control = resources()
        stream.connect.side_effect = OSError("connect failed")
        report = {}
        with self.assertRaisesRegex(OSError, "connect failed"):
            await transmit_tone(stream, control, ADDRESS, SERVICE, [], report, settle=0)
        stream.write.assert_not_awaited()
        stream.close.assert_awaited_once()
        control.disconnect.assert_awaited_once()

    async def test_already_transmitting_radio_is_not_interrupted(self):
        stream, control = resources()
        control.get_status.return_value = status(True)
        with self.assertRaisesRegex(RuntimeError, "already transmitting"):
            await transmit_tone(stream, control, ADDRESS, SERVICE, [], {}, settle=0)
        stream.connect.assert_not_awaited()
        stream.write.assert_not_awaited()

    async def test_unverified_stop_is_reported_as_failure(self):
        stream, control = resources()
        control.get_status.side_effect = [status(), status(True), status(True), status(True)]
        report = {}
        with patch("gps_rssi.audio_test.stream_tone", AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "stop could not be verified"):
                await transmit_tone(stream, control, ADDRESS, SERVICE, [], report, settle=0)
        self.assertFalse(report["stop_verified"])
        stream.close.assert_awaited_once()

    async def test_stop_write_failure_still_attempts_remaining_cleanup(self):
        stream, control = resources()
        stream.write.side_effect = OSError("link lost")
        control.get_status.side_effect = OSError("control lost")
        result = await cleanup_audio(stream, control, stop=True, settle=0)
        self.assertFalse(result["stop_verified"])
        self.assertEqual(len(result["cleanup_errors"]), 5)
        stream.close.assert_awaited_once()
        control.disconnect.assert_awaited_once()

    async def test_stream_is_paced_and_stalled_writes_have_a_timeout(self):
        stream, _ = resources()
        started = asyncio.get_running_loop().time()
        await stream_tone(stream, [(b"frame", 320), (b"frame", 320)])
        self.assertGreaterEqual(asyncio.get_running_loop().time() - started, .018)
        async def stalled_write(_):
            await asyncio.Event().wait()
        stream.write.side_effect = stalled_write
        with self.assertRaises(TimeoutError):
            await stream_tone(stream, [(b"frame", 320)], write_timeout=.01)


@unittest.skipUnless(HAS_AUDIO, "Optional audio dependencies not installed")
class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_variant_three_decodes_without_transmitting_and_tracks_segments(self):
        from gps_rssi.audio_signal import tone_pcm, encode_sbc
        packets = encode_sbc(tone_pcm(duration=.5))
        wire = b"".join(b"\x7e\x03" + audio_packet(data)[2:] for data, _ in packets) + END_AUDIO
        stream, control = resources()
        sent = False
        async def read():
            nonlocal sent
            if not sent:
                sent = True
                return wire
            await asyncio.Event().wait()
        stream.read.side_effect = read
        report = dict(segments=[], received_packets=0)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(32000)
            await receive_audio(stream, control, ADDRESS, SERVICE, wav, report, duration=.03)
        self.assertEqual(report["decoded_samples"], 16000)
        self.assertEqual(len(report["segments"]), 1)
        self.assertEqual(report["segments"][0]["closed_by"], "audio_end")
        stream.write.assert_not_awaited()

    async def test_local_transmit_echo_is_rejected(self):
        stream, control = resources()
        stream.read.side_effect = AsyncMock(return_value=b"\x7e\x09echo\x7e")
        report = dict(segments=[], received_packets=0)
        with wave.open(io.BytesIO(), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(32000)
            with self.assertRaisesRegex(RuntimeError, "transmit echo"):
                await receive_audio(stream, control, ADDRESS, SERVICE, wav, report, duration=.03)
        stream.write.assert_not_awaited()


@unittest.skipUnless(HAS_AUDIO, "Optional audio dependencies not installed")
class AudioCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_default_tone_creates_files_without_a_radio_connection(self):
        output = self.root / "tone"
        with patch("gps_rssi.audio_test.discover_services") as discover, redirect_stdout(io.StringIO()):
            run(parser().parse_args(["tone", "--output", str(output), "--duration", ".5"]))
        discover.assert_not_called()
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["state"], "prepared")
        self.assertFalse(report["transmission_requested"])
        self.assertTrue((output / "tone.sbc").is_file())
        with self.assertRaises(FileExistsError):
            run(parser().parse_args(["tone", "--output", str(output)]))

    def test_invalid_or_incomplete_transmit_options_fail_before_connections(self):
        from gps_rssi.audio_test import run_tone
        for options in (dict(transmit=True), dict(address=ADDRESS), dict(duration=11),
                        dict(transmit=True, address=ADDRESS, connect_timeout=float("nan"))):
            output = self.root / "invalid"
            with patch("gps_rssi.audio_test.discover_services") as discover, self.assertRaises(ValueError):
                run_tone(output, **options)
            discover.assert_not_called()
            self.assertFalse(output.exists())

    def test_failed_discovery_keeps_prepared_tone_and_explicit_failure_report(self):
        from gps_rssi.audio_test import run_tone
        output = self.root / "failed"
        with patch("gps_rssi.audio_test.discover_services", AsyncMock(side_effect=RuntimeError("no service"))):
            with self.assertRaisesRegex(RuntimeError, "no service"):
                run_tone(output, transmit=True, address=ADDRESS, duration=.5)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["state"], "failed")
        self.assertFalse(report["stop_attempted"])
        self.assertTrue((output / "tone.wav").is_file())


if __name__ == "__main__":
    unittest.main()
