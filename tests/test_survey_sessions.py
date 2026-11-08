import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from gps_rssi.field_recording import field_recording
from gps_rssi.measurements import utc_now
from gps_rssi.session_results import build_results
from gps_rssi.sharing import merge_databases, snapshot_sessions
from gps_rssi.storage import Store, load_log
from gps_rssi.survey_sessions import SurveySessions, session_path
from gps_rssi.workspace import Workspace
from gps_rssi.workspace_jobs import Jobs


def sample(value=50):
    now = utc_now()
    return dict(status_received_at=now, status=dict(rssi=value, is_in_tx=False, is_in_rx=True,
        is_sq=True, is_gps_locked=True, curr_ch_id=1),
        channel=dict(channel_id=1, rx_freq=146.52, tx_freq=146.52),
        position=dict(latitude=33.96, longitude=-117.95, accuracy=8, time=now))


class SurveySessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.a = self.workspace.save_profile(dict(name='Vehicle', address='AA:BB:CC:DD:EE:01', role='mobile', gps=True, label='Roof antenna'))
        self.b = self.workspace.save_profile(dict(name='Site', address='AA:BB:CC:DD:EE:02', role='fixed', gps=False))

    def create(self, profiles=None, name='Ridge survey', notes='Antenna 2 m\nDry weather'):
        return self.workspace.sessions.create(dict(name=name, notes=notes, profiles=profiles or [self.a['id']]), self.workspace.profiles())

    def operation(self, session, *, state='stopped', profile=None, report=None, kind='transmit'):
        jobs = self.workspace.jobs
        key = f'{len(jobs.entries)+1:032x}'
        folder = jobs.directory/key/'result'
        folder.mkdir(parents=True)
        profile = profile or self.a
        jobs.entries[key] = dict(id=key, kind=kind, title='Field survey · '+profile['name'], profile=profile['id'],
            survey_session=session['id'], state=state, created_at=utc_now(), finished_at=utc_now() if state=='stopped' else None,
            resources=['radio:'+profile['address']], forced=False, stop_requested=False, message='Test operation')
        (folder/'report.json').write_text(json.dumps(report or dict(recording=True, samples_recorded=0, stop_attempted=False, stop_verified=None)))
        jobs._save(jobs.entries[key])
        return key

    def wait_session(self, key, state):
        deadline = time.monotonic()+12
        while time.monotonic()<deadline:
            entry = self.workspace.sessions.describe(key)
            if entry['state']==state:
                return entry
            threading.Event().wait(.02)
        self.fail(f"Session did not reach {state}: {entry}")

    def test_creation_is_persistent_and_starts_no_radio(self):
        with patch.object(self.workspace.jobs, 'launch') as launch:
            entry = self.create([self.a['id'], self.b['id']])
        launch.assert_not_called()
        self.assertEqual(entry['state'], 'open')
        restored = Workspace(self.root).sessions.describe(entry['id'])
        self.assertEqual(restored['notes'], 'Antenna 2 m\nDry weather')
        self.assertEqual(restored['radios'][0]['label'], 'Roof antenna')
        self.assertEqual(len(restored['radios']), 2)

    def test_invalid_names_members_and_paths_are_rejected(self):
        for data in (dict(name='', profiles=[self.a['id']]), dict(name='Test', profiles=[]),
                     dict(name='Test', profiles=['../../outside']), dict(name='Test', profiles=[self.a['id']], notes='x'*4001)):
            with self.assertRaises(ValueError):
                self.workspace.sessions.create(data, self.workspace.profiles())
        with self.assertRaises(ValueError):
            self.workspace.sessions.finish('../../outside')

    def test_recording_identity_and_run_notes_are_retained_in_database(self):
        entry = self.create()
        report = {}
        with field_recording(self.root/self.a['id'], self.a['address'], 'mobile', report, session_path(self.root, entry['id'])) as recorder:
            recorder.store.sample(sample())
        sessions, samples, _ = load_log(self.root/self.a['database'])
        self.assertEqual(sessions[0]['id'], report['recording_session_id'])
        self.assertEqual(json.loads(sessions[0]['metadata_json'])['survey_session']['name'], entry['name'])
        self.assertEqual(report['survey_session_id'], entry['id'])
        self.assertEqual(len(samples), 1)

    def test_named_preflight_and_review_keep_their_session_ownership(self):
        entry = self.create()
        with patch.object(self.workspace.jobs, 'launch', return_value={}) as launch:
            self.workspace.launch(dict(kind='preflight', profile=self.a['id'], survey_session=entry['id']))
        self.assertEqual(launch.call_args.kwargs['survey_session'], entry['id'])
        self.operation(entry, kind='preflight', state='completed', report=dict(ready=True, checked_at=utc_now(), profile_revision=self.a['revision']))
        review = self.workspace.prepare_transmit(dict(profile=self.a['id'], automatic=True, field=True, survey_session=entry['id']))
        with patch.object(self.workspace.jobs, 'launch', return_value={}) as launch:
            self.workspace.launch(dict(kind='transmit', intent=review['intent'], confirmed=True, survey_session='ignored'))
        args = launch.call_args.args[1](self.root/'output')
        self.assertEqual(launch.call_args.kwargs['survey_session'], entry['id'])
        self.assertEqual(args[-2:], ['--survey-session', str(session_path(self.root, entry['id']))])
        self.assertEqual(review['survey_name'], entry['name'])

    def test_finish_revokes_a_previously_reviewed_start(self):
        entry = self.create()
        self.operation(entry, kind='preflight', state='completed', report=dict(ready=True, checked_at=utc_now(), profile_revision=self.a['revision']))
        review = self.workspace.prepare_transmit(dict(profile=self.a['id'],automatic=True,field=True,survey_session=entry['id']))
        with patch('gps_rssi.survey_sessions.threading.Thread'):
            self.workspace.sessions.finish(entry['id'])
        with patch.object(self.workspace.jobs,'launch') as launch:
            with self.assertRaisesRegex(ValueError,'closed'):
                self.workspace.launch(dict(kind='transmit',intent=review['intent'],confirmed=True))
            launch.assert_not_called()

    def test_finished_or_changed_sessions_cannot_start_new_operations(self):
        entry = self.create()
        with self.assertRaisesRegex(ValueError, 'belonging'):
            self.workspace.launch(dict(kind='preflight', profile=self.b['id'], survey_session=entry['id']))
        path = self.root/self.a['id']
        path.write_text(path.read_text()+'\n# changed setup\n')
        with self.assertRaisesRegex(ValueError, 'setup changed'):
            self.workspace.launch(dict(kind='preflight', profile=self.a['id'], survey_session=entry['id']))
        self.workspace.sessions.entries[entry['id']]['state']='finished'
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.workspace.launch(dict(kind='preflight', profile=self.a['id'], survey_session=entry['id']))

    def test_finish_stops_only_owned_operations_and_waits_for_cleanup(self):
        entry = self.create()
        other = self.create([self.b['id']], name='Other run')
        own = self.operation(entry, state='running')
        unrelated = self.operation(other, state='running', profile=self.b)
        def stop(key):
            self.workspace.jobs.entries[key]['state']='stopping'
        with patch.object(self.workspace.jobs, 'stop', side_effect=stop) as stopping:
            self.workspace.sessions.finish(entry['id'])
            self.workspace.sessions.finish(entry['id'])
            self.assertEqual(self.workspace.sessions.entries[entry['id']]['state'], 'finishing')
            self.assertIsNone(self.workspace.sessions.entries[entry['id']]['result_job'])
            stopping.assert_called_once_with(own)
            self.workspace.jobs.entries[own]['state']='stopped'
            finished = self.wait_session(entry['id'], 'finished')
        self.assertEqual(self.workspace.jobs.entries[unrelated]['state'], 'running')
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            finished=self.workspace.sessions.describe(entry['id'])
            if finished.get('result') and finished['result']['state'] not in ('running','stopping'):
                break
            threading.Event().wait(.02)
        self.assertEqual(finished['result']['state'], 'completed', finished)
        self.assertIn('index.html', finished['result']['files'])
        self.workspace.sessions.finish(entry['id'])
        self.assertEqual(sum(j['kind']=='session-results' for j in self.workspace.jobs.entries.values()), 1)

    def test_unknown_tx_blocks_results_until_manual_acknowledgement(self):
        entry = self.create()
        job = self.operation(entry, state='unknown', report=dict(stop_attempted=True, stop_verified=False))
        with patch.object(self.workspace.jobs, 'launch') as launch:
            self.workspace.sessions.finish(entry['id'])
            result = self.wait_session(entry['id'], 'attention')
            launch.assert_not_called()
        self.assertIn('not verified', result['message'])
        self.workspace.jobs.acknowledge(job, True)
        self.assertEqual(self.workspace.jobs.entries[job]['state'], 'interrupted')

    def test_restart_keeps_interrupted_finish_for_explicit_recovery(self):
        entry = self.create()
        operation = self.operation(entry, state='running')
        stored = self.workspace.sessions.entries[entry['id']]
        stored['state']='finishing'
        self.workspace.sessions.save(stored)
        restored = Workspace(self.root)
        self.assertEqual(restored.sessions.entries[entry['id']]['state'], 'attention')
        self.assertEqual(restored.jobs.entries[operation]['state'], 'unknown')
        self.assertFalse(restored.jobs.processes)

    def make_recording(self, value):
        store = Store(self.root/self.a['database'], self.a['name'], 'mobile', 'benlink_bluetooth', {})
        try:
            store.sample(sample(value))
            return store.session_id
        finally:
            store.close()

    def test_results_exclude_earlier_and_later_runs_in_same_database(self):
        old = self.make_recording(10)
        entry = self.create(name='<script>survey</script>', notes='Roof & mast\n<script>alert(1)</script>')
        selected = self.make_recording(50)
        later = self.make_recording(90)
        self.operation(entry, report=dict(recording=True, recording_session_id=selected, samples_recorded=1, tones_sent=2, stop_verified=True))
        manifest=self.workspace.sessions.entries[entry['id']]
        manifest.update(state='finished', finished_at=utc_now(), operations=self.workspace.sessions.operations(entry['id']))
        self.workspace.sessions.save(manifest)
        output=self.root/'results'
        summary=build_results(self.root, entry['id'], output)
        self.assertEqual(summary['selected_samples'], 1)
        sessions, samples, _=load_log(output/'recordings.sqlite')
        self.assertEqual([s['id'] for s in sessions], [selected])
        self.assertEqual([s['rssi_value'] for s in samples], [50])
        self.assertNotIn(old, [s['id'] for s in sessions])
        self.assertNotIn(later, [s['id'] for s in sessions])
        self.assertEqual(len(load_log(self.root/self.a['database'])[0]), 3)
        page=(output/'index.html').read_text()
        self.assertIn('&lt;script&gt;survey&lt;/script&gt;', page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertTrue((output/'map.html').is_file())

    def test_snapshot_preserves_merge_provenance_and_missing_ids_fail_closed(self):
        keep, discard = self.make_recording(10), self.make_recording(90)
        merged=self.root/'merged.sqlite'
        merge_databases([self.root/self.a['database']], merged)
        filtered=self.root/'filtered.sqlite'
        snapshot_sessions(merged, filtered, [keep])
        with sqlite3.connect(filtered) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('SELECT DISTINCT session_id FROM samples_origins').fetchall(), [(keep,)])
        self.assertEqual([s['id'] for s in load_log(merged)[0]], [keep, discard])
        with self.assertRaisesRegex(ValueError, 'missing'):
            snapshot_sessions(merged, self.root/'missing.sqlite', ['missing'])
        self.assertFalse((self.root/'missing.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
