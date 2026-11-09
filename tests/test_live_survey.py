from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gps_rssi.live_survey import LiveSurvey
from gps_rssi.storage import Store
from gps_rssi.workspace import Workspace
from results_fixtures import CompletedResultsFixture, frame


class LiveSurveyTests(CompletedResultsFixture, unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.workspace=Workspace(self.root);self.counter=0
        self.profile=self.workspace.save_profile(dict(name='Vehicle',role='mobile',gps=True,address='AA:BB:CC:DD:EE:01'))
        self.live=LiveSurvey(self.workspace)

    def test_named_sweep_uses_only_its_recording_identities(self):
        a,_,ids=self.completed('A',recordings=2)
        self.completed('B','active')
        with closing(Store(self.root/self.profile['database'],'Unmanaged','mobile','benlink_bluetooth',{})) as store:
            store.sample(frame('2026-11-09T12:00:00Z',rssi=99))
        with patch('gps_rssi.benlink_radio.create_connection',side_effect=AssertionError('no radio')):
            result=self.live.snapshot(a['id'])
        row=result['radios'][0]
        self.assertEqual(len(row['records']),6)
        self.assertEqual(set(r['session'] for r in row['records']),set(ids))
        self.assertEqual(row['sweep_types'],['passive'])
        self.assertEqual(sum(r['rssi'] is not None for r in row['records']),4)
        self.assertEqual(sum(r['lat'] is None for r in row['records']),2)
        self.assertEqual(row['job']['state'],'stopped')

    def test_default_selects_latest_managed_run_not_cumulative_database(self):
        self.completed('Earlier')
        _,_,ids=self.completed('Later','active')
        row=self.live.snapshot()['radios'][0]
        self.assertEqual(len(row['records']),3)
        self.assertEqual(row['recording_ids'],ids)
        self.assertEqual(row['sweep_types'],['active'])

    def test_no_managed_recording_does_not_show_old_profile_samples(self):
        with closing(Store(self.root/self.profile['database'],'Vehicle','mobile','benlink_bluetooth',{})) as store:
            store.sample(frame('2026-11-08T12:00:00Z'))
        row=self.live.snapshot()['radios'][0]
        self.assertIsNone(row['latest']);self.assertFalse(row['records'])
        self.assertIsNone(row['profile']['latest'])

    def test_new_recording_waiting_for_identity_does_not_reuse_previous_run(self):
        self.completed('Earlier')
        key=self.key();folder=self.workspace.jobs.directory/key/'result';folder.mkdir(parents=True)
        entry=dict(id=key,kind='record',title='Connecting',profile=self.profile['id'],state='running',created_at='2026-11-10T00:00:00Z',resources=[])
        self.workspace.jobs.entries[key]=entry;self.workspace.jobs._save(entry)
        row=self.live.snapshot()['radios'][0]
        self.assertFalse(row['records']);self.assertIsNone(row['latest'])
        self.assertEqual(row['job']['id'],key)

    def test_fixed_receiver_has_signal_but_no_mobile_map_position(self):
        fixed=self.workspace.save_profile(dict(name='Site',role='fixed',gps=False,address='AA:BB:CC:DD:EE:02'))
        session,_,_=self.completed('Site test',profile=fixed)
        row=self.live.snapshot(session['id'])['radios'][0]
        self.assertEqual(row['latest']['rssi_value'],60)
        self.assertTrue(all(r['lat'] is None and r['rssi'] is None for r in row['records']))
        self.assertIsNone(row['latest_fix'])

    def test_reconnect_breaks_route_even_with_short_sample_gap(self):
        session,_,ids=self.completed('Reconnect')
        with closing(__import__('sqlite3').connect(self.root/self.profile['database'])) as db:
            db.execute('INSERT INTO events VALUES(NULL,?,?,?,?)',(ids[0],'2026-11-08T12:00:49Z','connected','{}'));db.commit()
        row=self.live.snapshot(session['id'])['radios'][0]
        self.assertTrue(row['records'][-1]['break_before'])

    def test_missing_database_is_reported_per_radio_without_creating_a_file(self):
        session,_,_=self.completed('Missing')
        database=self.root/self.profile['database'];database.unlink()
        row=self.live.snapshot(session['id'])['radios'][0]
        self.assertIn('unavailable',row['error']);self.assertFalse(database.exists())

    def test_snapshot_is_bounded_and_marks_recent_window(self):
        session,_,_=self.completed('Bounded')
        with patch('gps_rssi.live_survey.MAX_SAMPLES',2):
            row=self.live.snapshot(session['id'])['radios'][0]
        self.assertEqual(len(row['records']),2);self.assertTrue(row['truncated'])
        self.assertEqual(row['records'][-1]['rssi'],60)

    def test_invalid_scope_is_rejected(self):
        for key in ('../../outside','f'*32):
            with self.assertRaises(ValueError):self.live.snapshot(key)

    def test_snapshot_reads_committed_wal_and_does_not_modify_source(self):
        session,_,ids=self.completed('Live append')
        import sqlite3
        path=self.root/self.profile['database']
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            columns=','.join(row[1] for row in db.execute('PRAGMA table_info(samples)') if row[1]!='id')
            db.execute(f'INSERT INTO samples ({columns}) SELECT {columns} FROM samples WHERE id=(SELECT max(id) FROM samples)')
            db.commit()
            before=db.execute('SELECT count(*) FROM samples').fetchone()[0]
            result=self.live.snapshot(session['id'])
            self.assertEqual(len(result['radios'][0]['records']),4)
            self.assertEqual(db.execute('SELECT count(*) FROM samples').fetchone()[0],before)
