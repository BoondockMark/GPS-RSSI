import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gps_rssi.automatic import automatic_tones
from gps_rssi.field_check import inspect_radio
from gps_rssi.field_recording import field_recording
from gps_rssi.measurements import utc_now
from gps_rssi.storage import load_log
from gps_rssi.workspace import Workspace


def model(data):
    return SimpleNamespace(**data, model_dump=lambda **_: data)


def resources():
    status = model(dict(is_in_tx=False, is_in_rx=True, is_sq=True,
        is_gps_locked=True, rssi=50, curr_ch_id=1))
    channel = model(dict(channel_id=1, rx_freq=146.52, tx_freq=146.52))
    position = model(dict(latitude=33.96, longitude=-117.95, accuracy=8, time=utc_now()))
    async def read():
        await asyncio.Event().wait()
    control = Mock(connect=AsyncMock(), disconnect=AsyncMock(), get_status=AsyncMock(return_value=status),
        get_channel=AsyncMock(return_value=channel), get_position=AsyncMock(return_value=position))
    stream = Mock(connect=AsyncMock(), close=AsyncMock(), write=AsyncMock(), read=read)
    return stream, control


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_connects_and_disconnects_without_any_audio_write(self):
        stream, control = resources()
        report = dict(checks=[], ready=False)
        now = datetime.now(timezone.utc)
        with patch('gps_rssi.field_check.discover_services', AsyncMock(return_value=[])), \
             patch('gps_rssi.field_check.select_audio_service', return_value=dict(channel=2)), \
             patch('gps_rssi.field_check.clock_reference', AsyncMock(return_value=(now, 0, 'computer', 'Radio GPS is not locked'))):
            await inspect_radio(control, stream, 'address', report)
        self.assertTrue(report['ready'])
        self.assertEqual(report['checks'][-1]['state'], 'attention')
        stream.write.assert_not_awaited()
        stream.close.assert_awaited_once()
        control.disconnect.assert_awaited_once()

    async def test_radio_in_tx_is_left_alone_and_blocks_start(self):
        stream, control = resources()
        control.get_status.return_value.is_in_tx = True
        report = dict(checks=[], ready=False)
        await inspect_radio(control, stream, 'address', report)
        self.assertFalse(report['ready'])
        self.assertIn('Release PTT', report['checks'][-1]['detail'])
        stream.connect.assert_not_awaited()
        stream.write.assert_not_awaited()

    async def test_failed_audio_check_closes_connections_and_blocks_start(self):
        stream, control = resources()
        report = dict(checks=[], ready=False)
        with patch('gps_rssi.field_check.discover_services', AsyncMock(side_effect=OSError('not paired'))):
            await inspect_radio(control, stream, 'address', report)
        self.assertFalse(report['ready'])
        self.assertIn('not paired', report['checks'][-1]['detail'])
        stream.write.assert_not_awaited()
        control.disconnect.assert_awaited_once()


class FieldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.profile = self.workspace.save_profile(dict(name='Vehicle', address='AA:BB:CC:DD:EE:01', role='mobile', gps=True))

    def check_result(self, **updates):
        key = f'{len(self.workspace.jobs.entries)+1:032x}'
        folder = self.workspace.jobs.directory / key / 'result'
        folder.mkdir(parents=True, exist_ok=True)
        self.workspace.jobs.entries[key] = dict(id=key, kind='preflight', profile=self.profile['id'],
            title='Preflight', state='completed', created_at=utc_now(), resources=[])
        report = dict(ready=True, checked_at=utc_now(), profile_revision=self.profile['revision'])
        report.update(updates)
        (folder / 'report.json').write_text(json.dumps(report))

    def test_field_start_requires_recent_matching_successful_check(self):
        payload = dict(profile=self.profile['id'], automatic=True, field=True)
        with self.assertRaisesRegex(ValueError, 'preflight'):
            self.workspace.prepare_transmit(payload)
        for updates in (dict(ready=False), dict(profile_revision='old'),
                        dict(checked_at=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat())):
            self.check_result(**updates)
            with self.assertRaisesRegex(ValueError, 'preflight'):
                self.workspace.prepare_transmit(payload)
        self.check_result()
        review = self.workspace.prepare_transmit(payload)
        with patch.object(self.workspace.jobs, 'launch', return_value={}) as launch:
            self.workspace.launch(dict(kind='transmit', intent=review['intent'], confirmed=True))
        args = launch.call_args.args[1](self.root/'out')
        self.assertEqual(args[-2:], ['--record-config', str(self.root/self.profile['id'])])
        self.assertTrue(any(r.startswith('database:') for r in launch.call_args.kwargs['resources']))

    def test_recording_saves_qualified_receive_samples_and_closes_on_error(self):
        _, control = resources()
        report = {}
        with self.assertRaisesRegex(RuntimeError, 'test halt'):
            with field_recording(self.root/self.profile['id'], self.profile['address'], 'mobile', report) as recorder:
                asyncio.run(recorder.sample(control))
                raise RuntimeError('test halt')
        sessions, samples, events = load_log(self.root/self.profile['database'])
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['direction'], 'downlink')
        self.assertEqual(samples[0]['rx_frequency_hz'], 146520000)
        self.assertEqual(samples[0]['latitude'], 33.96)
        self.assertEqual(report['samples_recorded'], 1)
        self.assertEqual(events[-1]['kind'], 'automatic_finished')
        self.assertEqual(sessions[0]['source'], 'benlink_bluetooth')
        control.connect.assert_not_awaited()

    def test_preflight_rejects_damaged_recording_without_changing_it(self):
        from gps_rssi.field_check import run_check
        database = self.root/self.profile['database']
        database.parent.mkdir(parents=True, exist_ok=True)
        original = b'not a recording database'
        database.write_bytes(original)
        with patch('gps_rssi.field_check.encode_sbc'), patch('gps_rssi.field_check.tone_pcm'), patch('gps_rssi.field_check.create_connection') as connect:
            report = run_check(self.root/'check', self.root/self.profile['id'])
        self.assertFalse(report['ready'])
        self.assertEqual(database.read_bytes(), original)
        connect.assert_not_called()

    def test_mismatched_recording_cannot_be_attached_to_a_radio(self):
        with self.assertRaisesRegex(ValueError, 'belong'):
            with field_recording(self.root/self.profile['id'], 'AA:BB:CC:DD:EE:FF', 'mobile', {}):
                pass
        self.assertFalse((self.root/self.profile['database']).exists())


class RecordingCadenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_recording_failure_halts_before_transmission(self):
        stream, control = resources()
        now = datetime.now(timezone.utc)
        recorder = SimpleNamespace(sample=AsyncMock(side_effect=TimeoutError('control timeout')))
        with patch('gps_rssi.automatic.clock_reference', AsyncMock(return_value=(now, asyncio.get_running_loop().time(), 'computer', None))):
            with self.assertRaisesRegex(TimeoutError, 'control timeout'):
                await automatic_tones(stream, control, 'address', {}, [], dict(tones_sent=0), role='fixed', recorder=recorder, settle=0)
        stream.write.assert_not_awaited()
        stream.close.assert_awaited_once()
        control.disconnect.assert_awaited_once()

    async def test_sampling_shares_control_serially_and_preserves_tone_deadlines(self):
        stream, control = resources()
        elapsed, starts, samples = [0.0], [], []
        real_sleep = asyncio.sleep
        async def sleep(seconds):
            elapsed[0] += seconds
            await real_sleep(0)
        async def sample(owner):
            self.assertIs(owner, control)
            samples.append(elapsed[0])
            elapsed[0] += .3
        async def send(*args):
            starts.append(elapsed[0])
            elapsed[0] += 5
            if len(starts) == 3:
                raise RuntimeError('test halt')
        now = datetime(2026, 11, 8, 12, 59, 50, tzinfo=timezone.utc)
        report = dict(tones_sent=0)
        with patch('gps_rssi.automatic.asyncio.get_running_loop', return_value=SimpleNamespace(time=lambda: elapsed[0])), \
             patch('gps_rssi.automatic.asyncio.sleep', sleep), \
             patch('gps_rssi.automatic.clock_reference', AsyncMock(return_value=(now, 0, 'radio GPS', None))), \
             patch('gps_rssi.automatic.stream_tone', send):
            with self.assertRaisesRegex(RuntimeError, 'test halt'):
                await automatic_tones(stream, control, 'address', {}, [], report, role='fixed',
                    settle=1.5, recorder=SimpleNamespace(sample=sample))
        self.assertEqual(starts, [10, 30, 50])
        self.assertGreater(len(samples), 10)
        for sample_at in samples:
            self.assertFalse(any(start-3 <= sample_at < start+6.5 for start in starts))
        control.connect.assert_awaited_once()
        self.assertTrue(report['stop_verified'])


if __name__ == '__main__':
    unittest.main()
