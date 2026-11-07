"""Frequency capture, old-log compatibility, and export preservation."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from gps_rssi.benlink_radio import collect
from gps_rssi.dashboard import snapshot as dashboard_snapshot
from gps_rssi.measurements import FREQUENCY_FIELDS, channel_frequencies, select_points
from gps_rssi.sharing import merge_databases, snapshot
from gps_rssi.storage import Store, export_csv, load_log
from gps_rssi.survey import create_survey
from test_logger import FRAME, FakeConnection, Model


def frame(rx=146.52, tx=146.12, *, transmit=False, talk_around=False):
    result = copy.deepcopy(FRAME)
    result['status']['is_in_tx'] = transmit
    result['channel'] = dict(channel_id=1, rx_freq=rx, tx_freq=tx, talk_around=talk_around)
    result['status_received_at'] = result['position']['time']
    return result


class FrequencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'radio.sqlite'

    def store(self):
        store = Store(self.path, 'radio', 'mobile', 'replay', {})
        self.addCleanup(store.close)
        return store

    def legacy_log(self):
        store = self.store()
        store.sample(FRAME)
        # Reproduce the previous released schema without changing its sample bytes.
        for name in FREQUENCY_FIELDS:
            store.db.execute(f'ALTER TABLE samples DROP COLUMN {name}')
        store.db.commit()
        return store

    def test_hz_units_split_tx_rx_talkaround_and_unknown(self):
        self.assertEqual(channel_frequencies(frame()), dict(frequency_hz=146520000, rx_frequency_hz=146520000, tx_frequency_hz=146120000))
        self.assertEqual(channel_frequencies(frame(transmit=True))['frequency_hz'], 146120000)
        self.assertEqual(channel_frequencies(frame(transmit=True, talk_around=True))['frequency_hz'], 146520000)
        self.assertEqual(channel_frequencies(frame(rx=0))['frequency_hz'], None)
        self.assertEqual(channel_frequencies(FRAME), dict.fromkeys(FREQUENCY_FIELDS))

    def test_invalid_or_wrong_channel_values_are_rejected(self):
        for bad in (True, -1, float('nan'), float('inf'), '146.52', 1074):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store().sample(frame(rx=bad))
        bad = frame()
        bad['channel']['channel_id'] = 2
        with self.assertRaises(ValueError):
            channel_frequencies(bad)

    def test_storage_raw_csv_and_points_preserve_frequency(self):
        self.store().sample(frame())
        sessions, samples, _ = load_log(self.path)
        self.assertEqual(samples[0]['frequency_hz'], 146520000)
        self.assertEqual(self.store().db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(json.loads(samples[0]['raw_json'])['channel']['rx_freq'], 146.52)
        self.assertEqual(select_points(sessions, samples)[0][0]['tx_frequency_hz'], 146120000)
        live = dashboard_snapshot(self.path, source='replay')
        self.assertEqual(live['latest']['frequency_hz'], 146520000)
        self.assertEqual(live['points'][0]['frequency_hz'], 146520000)
        output = self.root / 'samples.csv'
        export_csv(self.path, output)
        with output.open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['frequency_hz'], '146520000')
        self.assertEqual(row['tx_frequency_hz'], '146120000')

    def test_old_logs_remain_readable_exportable_and_upgrade_without_backfill(self):
        old = self.legacy_log()
        before = old.db.execute('SELECT raw_json FROM samples').fetchone()[0]
        self.assertIsNone(load_log(self.path)[1][0]['frequency_hz'])
        export_csv(self.path, self.root / 'old.csv')
        with (self.root / 'old.csv').open() as stream:
            self.assertEqual(next(csv.DictReader(stream))['frequency_hz'], '')
        snapshot(self.path, self.root / 'old-snapshot.sqlite')
        upgraded = self.store()
        upgraded.sample(frame())
        self.assertEqual(upgraded.db.execute('SELECT id, frequency_hz FROM samples ORDER BY id').fetchall(), [(1, None), (2, 146520000)])
        self.assertEqual(upgraded.db.execute('SELECT raw_json FROM samples WHERE id=1').fetchone()[0], before)

    def test_mixed_schema_merge_and_survey_exports_keep_frequency(self):
        self.legacy_log()
        snapshot(self.path, self.root / 'legacy.sqlite')
        current = self.store()
        current.sample(frame())
        output = self.root / 'merged.sqlite'
        counts = merge_databases([self.root / 'legacy.sqlite', self.path], output)
        self.assertEqual(counts['samples'], 2)
        self.assertEqual(counts['duplicate_samples'], 1)
        self.assertEqual([r['frequency_hz'] for r in load_log(output)[1]], [None, 146520000])
        bundle = self.root / 'survey'
        create_survey([output], bundle, source='replay', max_age=1e9)
        geo = json.loads((bundle / 'measurements.geojson').read_text())
        self.assertEqual(geo['features'][-1]['properties']['frequency_hz'], 146520000)
        self.assertIn('146520000', (bundle / 'map.html').read_text())


class FrequencyAcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_settings_are_refreshed_without_reusing_old_frequency(self):
        class ChangingRadio(FakeConnection):
            async def get_channel(self, channel_id):
                self.calls.append('get_channel')
                return Model(dict(channel_id=channel_id, rx_freq=146.52 if self.calls.count('get_channel') == 1 else 446.0,
                                  tx_freq=446.0))
        with tempfile.TemporaryDirectory() as work:
            store = Store(Path(work) / 'radio.sqlite', 'a', 'mobile', 'live', {})
            try:
                conn = ChangingRadio()
                await collect(store, lambda: conn, interval=0, max_samples=2)
                self.assertEqual(store.db.execute('SELECT frequency_hz FROM samples ORDER BY id').fetchall(), [(146520000,), (446000000,)])
            finally:
                store.close()

    async def test_channel_timeout_keeps_sample_with_unknown_frequency_then_reconnects(self):
        class BrokenChannel(FakeConnection):
            async def get_channel(self, channel_id):
                raise TimeoutError()
        with tempfile.TemporaryDirectory() as work:
            store = Store(Path(work) / 'radio.sqlite', 'a', 'mobile', 'live', {})
            try:
                first, second = BrokenChannel(), FakeConnection()
                connections = iter([first, second])
                await collect(store, lambda: next(connections), interval=0, reconnect_delay=0, gps=True, max_samples=2)
                self.assertTrue(first.disconnected and second.disconnected)
                self.assertEqual(store.db.execute('SELECT frequency_hz FROM samples ORDER BY id').fetchall(), [(None,), (146520000,)])
                self.assertEqual(store.db.execute("SELECT count(*) FROM events WHERE kind='frequency_error'").fetchone()[0], 1)
            finally:
                store.close()
