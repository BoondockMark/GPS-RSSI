from contextlib import redirect_stdout
import copy
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gps_rssi.alignment import align_logs
from gps_rssi.cli import parser, run
from gps_rssi.combine import combine
from gps_rssi.storage import Store, load_log


BASE = datetime(2026, 11, 6, 17, tzinfo=timezone.utc)


def time(t):
    return (BASE + timedelta(seconds=t)).isoformat()


def frame(t, *, tx=False, gps=True):
    return {"status_requested_at": time(t - .1), "status_received_at": time(t + .1),
            "status": dict(rssi=0, is_in_tx=tx, is_in_rx=not tx, is_sq=False,
                           is_gps_locked=gps, curr_ch_id=1),
            "position": dict(latitude=33.96 + t / 10000, longitude=-117.95, time=time(t), accuracy=10) if gps else None}


def shifted(log, offset):
    result = copy.deepcopy(log)
    for row in result[1]:
        row['received_at'] = (datetime.fromisoformat(row['received_at']) + timedelta(seconds=offset)).isoformat()
        raw = json.loads(row['raw_json'])
        raw['status_requested_at'] = (datetime.fromisoformat(raw['status_requested_at']) + timedelta(seconds=offset)).isoformat()
        row['raw_json'] = json.dumps(raw)
    return result


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mobile, self.fixed = self.root / 'mobile.sqlite', self.root / 'fixed.sqlite'
        for path, role in ((self.mobile, 'mobile'), (self.fixed, 'fixed')):
            store = Store(path, role, role, 'benlink_bluetooth', {'recording': {'label': role + ' radio'}})
            try:
                for t in range(7):
                    store.sample(frame(t, tx=t < 3 if role == 'mobile' else t >= 3, gps=role == 'mobile'))
            finally:
                store.close()
        self.mlog, self.flog = load_log(self.mobile), load_log(self.fixed)

    def result(self, mobile=None, fixed=None, **options):
        return align_logs(mobile or self.mlog, fixed or self.flog, **options)

    def find(self, result, receiver, sample):
        return next(r for r in result['rows'] if r['receiver'] == receiver and r['receiver_sample'] == sample)

    def test_both_directions_use_mobile_track_including_tx_gps(self):
        result = self.result()
        uplink = self.find(result, 'fixed', 2)
        downlink = self.find(result, 'mobile', 5)
        for row in (uplink, downlink):
            self.assertEqual(row['status'], 'associated')
            self.assertEqual(row['rssi'], 0)
            self.assertFalse(row['squelch_open'])
            self.assertEqual(row['rssi_unit'], 'benshi_relative_0_100')
        self.assertEqual(uplink['gps_sample'], 2)
        self.assertEqual(uplink['gps_session'], self.mlog[0][0]['id'])
        self.assertEqual(uplink['transmitter_samples'], [1, 2, 3])
        self.assertAlmostEqual(uplink['lat'], 33.9601)
        self.assertEqual(downlink['direction'], 'downlink')
        self.assertEqual(downlink['transmitter_session'], self.flog[0][0]['id'])

    def test_receiver_frequency_is_preserved_in_combined_results(self):
        for sample in self.mlog[1]:
            sample.update(frequency_hz=146520000, rx_frequency_hz=146520000, tx_frequency_hz=146120000)
        row = self.find(self.result(), 'mobile', 5)
        self.assertEqual(row['frequency_hz'], 146520000)
        self.assertEqual(row['rx_frequency_hz'], 146520000)
        self.assertEqual(row['tx_frequency_hz'], 146120000)

    def test_explicit_clock_offsets_leave_gps_time_unchanged(self):
        result = self.result(shifted(self.mlog, 4), shifted(self.flog, 2), mobile_offset=-4, fixed_offset=-2)
        row = self.find(result, 'fixed', 2)
        self.assertEqual(row['status'], 'associated')
        self.assertEqual(row['gps_time'], time(1))
        self.assertEqual(row['corrected_time'], time(1))
        self.assertEqual(row['original_time'], time(3.1))

    def test_timezone_offsets_are_compared_by_instant(self):
        log = copy.deepcopy(self.flog)
        for row in log[1]:
            row['received_at'] = datetime.fromisoformat(row['received_at']).astimezone(timezone(timedelta(hours=-8))).isoformat()
        self.assertEqual(self.result(fixed=log)['counts'], self.result()['counts'])

    def test_transition_and_missing_brackets_are_unconfirmed(self):
        row = self.find(self.result(), 'fixed', 3)
        self.assertEqual(row['status'], 'unconfirmed')
        self.assertEqual(row['reason'], 'opposite_radio_not_consistently_tx')
        self.assertEqual(self.find(self.result(), 'fixed', 1)['reason'], 'missing_tx_bracket')

    def test_reconnect_event_prevents_tx_bracket_across_interruption(self):
        mobile = copy.deepcopy(self.mlog)
        mobile[2].append(dict(session_id=mobile[0][0]['id'], received_at=time(1.5), kind='disconnected'))
        row = self.find(self.result(mobile=mobile), 'fixed', 2)
        self.assertNotEqual(row['status'], 'associated')
        self.assertEqual(row['reason'], 'missing_tx_bracket')

    def test_long_gap_and_nonoverlapping_logs_do_not_pair(self):
        result = self.result(fixed=shifted(self.flog, 100))
        self.assertTrue(all(r['status'] == 'unmatched' for r in result['rows']))
        self.assertIn('outside_overlap_or_connection_gap', result['reasons'])

    def test_clock_regression_invalidates_session(self):
        mobile = copy.deepcopy(self.mlog)
        mobile[1][4]['received_at'] = time(1)
        result = self.result(mobile=mobile)
        self.assertNotIn('associated', result['counts'])
        self.assertIn('clock_discontinuity_or_invalid_event_time', result['reasons'])

    def test_missing_or_slow_status_request_is_not_confident(self):
        for requested, reason in ((None, 'missing_or_invalid_status_time'), (time(-10), 'slow_status_request')):
            log = copy.deepcopy(self.flog)
            raw = json.loads(log[1][1]['raw_json'])
            raw['status_requested_at'] = requested
            log[1][1]['raw_json'] = json.dumps(raw)
            self.assertEqual(self.find(self.result(fixed=log), 'fixed', 2)['reason'], reason)

    def test_gps_conflicts_and_equidistant_fixes_are_rejected(self):
        for conflict, reason in ((True, 'conflicting_gps_fixes'), (False, 'ambiguous_gps_tie')):
            mobile = copy.deepcopy(self.mlog)
            if conflict:
                mobile[1][2]['position_time'] = time(1)
            else:
                mobile[1][1]['gps_locked'] = False
            self.assertEqual(self.find(self.result(mobile=mobile), 'fixed', 2)['reason'], reason)

    def test_bad_or_distant_gps_is_not_carried_forward(self):
        mobile = copy.deepcopy(self.mlog)
        for row in mobile[1]:
            row['gps_locked'] = False
        mobile[1][0]['gps_locked'] = True
        result = self.result(mobile=mobile, max_gps_gap=.5)
        self.assertEqual(self.find(result, 'fixed', 2)['reason'], 'gps_too_far_from_receive_time')
        mobile[1][0]['position_time'] = time(-50)
        result = self.result(mobile=mobile)
        self.assertEqual(self.find(result, 'fixed', 2)['reason'], 'no_valid_mobile_gps')
        self.assertEqual(result['rejected_gps']['stale_or_clock_skewed_fix'], 1)

    def test_duplicate_same_fix_is_deduplicated_but_overlap_is_ambiguous(self):
        mobile = copy.deepcopy(self.mlog)
        mobile[1][2].update(position_time=time(1), latitude=mobile[1][1]['latitude'])
        self.assertEqual(self.find(self.result(mobile=mobile), 'fixed', 2)['status'], 'associated')
        extra = copy.deepcopy(mobile[0][0])
        extra['id'] = 'overlapping-session'
        mobile[0].append(extra)
        mobile[1].extend([{**r, 'session_id': extra['id']} for r in list(mobile[1])])
        self.assertEqual(self.find(self.result(mobile=mobile), 'fixed', 2)['reason'], 'overlapping_sessions')

    def test_different_stations_require_explicit_selection(self):
        mobile = copy.deepcopy(self.mlog)
        mobile[0].append({**mobile[0][0], 'station': 'another-mobile', 'id': 'another'})
        with self.assertRaisesRegex(ValueError, 'exactly one mobile'):
            self.result(mobile=mobile)

    def test_large_sampling_gap_and_session_boundary_are_not_bridged(self):
        mobile = copy.deepcopy(self.mlog)
        mobile[1][:] = [mobile[1][0], mobile[1][-1]]
        self.assertEqual(self.find(self.result(mobile=mobile), 'fixed', 2)['reason'], 'outside_overlap_or_connection_gap')
        mobile = copy.deepcopy(self.mlog)
        second = {**mobile[0][0], 'id': 'next-session'}
        mobile[0].append(second)
        for row in mobile[1][2:]:
            row['session_id'] = second['id']
        self.assertNotEqual(self.find(self.result(mobile=mobile), 'fixed', 2)['status'], 'associated')

    def test_receive_interval_cannot_cross_start_of_opposite_segment(self):
        mobile = copy.deepcopy(self.mlog)
        mobile[1][:] = mobile[1][1:]
        raw = json.loads(mobile[1][0]['raw_json'])
        raw['status_requested_at'] = time(1)
        mobile[1][0]['raw_json'] = json.dumps(raw)
        # Fixed sample spans .9..1.1; the mobile segment only starts at 1.0.
        self.assertEqual(self.find(self.result(mobile=mobile), 'fixed', 2)['reason'], 'status_interval_crosses_boundary')

    def test_explicit_station_selection_and_empty_result_schema(self):
        store = Store(self.mobile, 'other-mobile', 'mobile', 'benlink_bluetooth', {})
        store.close()
        with self.assertRaises(ValueError):
            combine(self.mobile, self.fixed, self.root / 'ambiguous')
        self.assertFalse((self.root / 'ambiguous').exists())
        combine(self.mobile, self.fixed, self.root / 'selected', mobile_station='mobile')
        combine(self.mobile, self.fixed, self.root / 'empty', mobile_station='other-mobile')
        selected = (self.root / 'selected/measurements.csv').read_text().splitlines()[0]
        empty = (self.root / 'empty/measurements.csv').read_text().splitlines()[0]
        self.assertEqual(selected, empty)

    def test_same_database_and_invalid_options_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            combine(self.mobile, self.mobile, self.root / 'same')
        for options in ({'max_gap': 0}, {'fixed_offset': float('nan')}, {'max_request': float('inf')}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.result(**options)

    def test_gps_accuracy_and_rssi_validation_keep_rejection_reasons(self):
        mobile = copy.deepcopy(self.mlog)
        for row in mobile[1]:
            raw = json.loads(row['raw_json'])
            raw['position']['accuracy'] = 200
            row['raw_json'] = json.dumps(raw)
        result = self.result(mobile=mobile)
        self.assertEqual(result['rejected_gps']['poor_accuracy'], 7)
        self.assertEqual(self.find(result, 'fixed', 2)['reason'], 'no_valid_mobile_gps')
        fixed = copy.deepcopy(self.flog)
        fixed[1][1]['rssi_value'] = float('nan')
        self.assertEqual(self.find(self.result(fixed=fixed), 'fixed', 2)['reason'], 'unsupported_rssi')

    def test_unplaceable_interruption_and_huge_offset_cannot_associate(self):
        mobile = copy.deepcopy(self.mlog)
        mobile[2].append(dict(session_id=mobile[0][0]['id'], received_at='invalid', kind='connection_error'))
        self.assertNotIn('associated', self.result(mobile=mobile)['counts'])
        self.assertNotIn('associated', self.result(fixed_offset=1e100)['counts'])

    def test_cli_exports_reproducible_bundle_and_preserves_inputs(self):
        before = [p.read_bytes() for p in (self.mobile, self.fixed)]
        target = self.root / 'combined survey'
        args = parser().parse_args(['combine', '--mobile-db', str(self.mobile), '--fixed-db', str(self.fixed), '--output', str(target)])
        with redirect_stdout(io.StringIO()) as output:
            run(args)
        self.assertIn('associated:', output.getvalue())
        with (target / 'measurements.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(any(r['direction'] == 'uplink' and r['status'] == 'associated' for r in rows))
        summary = json.loads((target / 'summary.json').read_text())
        self.assertEqual(summary['inputs']['fixed'], str(self.fixed))
        self.assertEqual(summary['options']['max_gps_gap'], 2)
        self.assertEqual([p.read_bytes() for p in (self.mobile, self.fixed)], before)
        with self.assertRaises(FileExistsError):
            combine(self.mobile, self.fixed, target)

    def test_replay_is_excluded_by_default_and_can_be_selected_explicitly(self):
        for path, role in ((self.mobile, 'mobile'), (self.fixed, 'fixed')):
            store = Store(path, role, role, 'replay', {})
            store.sample(frame(20, gps=role == 'mobile'))
            store.close()
        live = combine(self.mobile, self.fixed, self.root / 'live')
        replay = combine(self.mobile, self.fixed, self.root / 'replay', source='replay')
        self.assertTrue(all(s['source'] != 'replay' for sessions in live['sessions'].values() for s in sessions))
        self.assertTrue(all(s['source'] == 'replay' for sessions in replay['sessions'].values() for s in sessions))

    def test_failed_output_cleans_its_own_bundle_and_keeps_inputs(self):
        target = self.root / 'failure'
        with patch('gps_rssi.combine.csv.DictWriter', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                combine(self.mobile, self.fixed, target)
        self.assertFalse(target.exists())
        self.assertTrue(self.mobile.exists())

    def test_map_escapes_labels_and_embeds_distinct_layers(self):
        from gps_rssi.combined_mapping import render_combined_map
        result = self.result()
        result['rows'][0]['receiver_label'] = '</script><script>alert(1)</script>__DATA__'
        page = render_combined_map(result['rows'], {'counts': result['counts']})
        self.assertNotIn('</script><script>alert(1)', page)
        self.assertIn('&lt;/script&gt;', page)
        self.assertIn('Mobile → fixed', page)
        self.assertIn('id="quality-filter"', page)
