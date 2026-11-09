from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gps_rssi.comparison_results import compare_results
from gps_rssi.result_metrics import result_quality
from gps_rssi.storage import Store, load_log
from gps_rssi.workspace import Workspace


from results_fixtures import CompletedResultsFixture, frame


class ResultsLibraryTests(CompletedResultsFixture, unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=Workspace(self.root)
        self.profile=self.workspace.save_profile(dict(name='Vehicle',role='mobile',gps=True,address='AA:BB:CC:DD:EE:01'))
        self.counter=0

    def test_catalog_lists_completed_sweeps_and_distinguishes_empty_and_fixed_only(self):
        a,_,_=self.completed('Mobile run')
        empty,_,_=self.completed('No samples',empty=True)
        fixed=self.workspace.save_profile(dict(name='Site',role='fixed',gps=False,address='AA:BB:CC:DD:EE:02'))
        fixed_run,_,_=self.completed('Site only',profile=fixed)
        self.workspace.sessions.create(dict(name='Open run',profiles=[self.profile['id']]),self.workspace.profiles())
        rows={row['id']:row for row in self.workspace.results.list()['results']}
        self.assertEqual(set(rows),{a['id'],empty['id'],fixed_run['id']})
        self.assertTrue(rows[a['id']]['compare_available'])
        self.assertEqual(rows[a['id']]['quality']['sample_count'],3)
        self.assertEqual(rows[a['id']]['quality']['gps_coverage_percent'],66.7)
        self.assertIsNone(rows[empty['id']]['quality']['duration_seconds'])
        self.assertFalse(rows[fixed_run['id']]['compare_available'])
        self.assertIsNone(rows[fixed_run['id']]['quality']['gps_coverage_percent'])

    def test_older_results_are_summarized_without_rewriting_them_and_cached(self):
        entry,folder,_=self.completed('Older result')
        summary=json.loads((folder/'summary.json').read_text())
        summary.pop('result_quality')
        (folder/'summary.json').write_text(json.dumps(summary))
        before=(folder/'summary.json').read_bytes()
        first=self.workspace.results.list()['results'][0]['quality']
        self.assertEqual(first['duration_seconds'],50)
        with patch('gps_rssi.results_library.result_quality',side_effect=AssertionError('must use cache')):
            second=self.workspace.results.list()['results'][0]['quality']
        self.assertEqual(first,second)
        self.assertEqual((folder/'summary.json').read_bytes(),before)

    def test_quality_counts_reported_errors_not_sampling_silence(self):
        database=self.root/'quality.sqlite'
        with closing(Store(database,'Vehicle','mobile','benlink_bluetooth',{})) as store:
            store.sample(frame('2026-11-08T12:00:00Z'))
            store.sample(frame('2026-11-08T12:10:00Z'))
            quality=result_quality(database)
            self.assertEqual(quality['connection_interruptions'],0)
            self.assertEqual(quality['duration_seconds'],600)
            for kind,at in [('connection_error','2026-11-08T12:00:05Z'),('connected','2026-11-08T12:00:10Z'),('connection_error','2026-11-08T12:05:00Z')]:
                store.db.execute('INSERT INTO events VALUES(NULL,?,?,?,?)',(store.session_id,at,kind,'{}'))
            store.db.commit()
        quality=result_quality(database,[dict(state='failed')])
        self.assertEqual(quality['connection_interruptions'],2)
        self.assertEqual(quality['unrecovered_interruptions'],1)
        self.assertEqual(quality['recovered_gap_seconds'],5)
        self.assertEqual(quality['failed_operations'],1)

    def test_date_boundary_preserves_original_sample_precision(self):
        database=self.root/'boundary.sqlite'
        with closing(Store(database,'Vehicle','mobile','benlink_bluetooth',{})) as store:
            store.sample(frame('2026-11-09T07:59:59.999600+00:00'))
        self.assertEqual(result_quality(database)['first_sample_at'],'2026-11-09T07:59:59.999600+00:00')

    def test_missing_or_invalid_summaries_do_not_hide_other_results(self):
        a,folder,_=self.completed('Unavailable')
        b,_,_=self.completed('Available')
        (folder/'summary.json').write_text('not JSON')
        rows={row['id']:row for row in self.workspace.results.list()['results']}
        self.assertIsNotNone(rows[a['id']]['problem'])
        self.assertTrue(rows[b['id']]['compare_available'])

    def test_comparison_uses_immutable_snapshots_and_defaults_to_different_runs(self):
        a,folder_a,ids_a=self.completed('Ambient','passive',recordings=2)
        b,folder_b,ids_b=self.completed('Tones','active')
        before=[hashlib.sha256((folder/'recordings.sqlite').read_bytes()).hexdigest() for folder in (folder_a,folder_b)]
        with closing(Store(self.root/self.profile['database'],'Later run','mobile','benlink_bluetooth',{})) as later:
            later.sample(frame('2026-11-09T12:00:00Z',rssi=99))
        output=self.root/'comparison'
        with patch('gps_rssi.benlink_radio.create_connection',side_effect=AssertionError('no radio')):
            summary=compare_results(self.root,a['id'],a['result_job'],b['id'],b['result_job'],output)
        self.assertEqual(summary['selected_samples'],9)
        self.assertIn(summary['comparison']['default_a'],ids_a)
        self.assertIn(summary['comparison']['default_b'],ids_b)
        self.assertTrue(summary['comparison']['mixed_sweep_types'])
        self.assertNotIn(99,[row['rssi_value'] for row in load_log(output/'recordings.sqlite')[1]])
        self.assertEqual(before,[hashlib.sha256((folder/'recordings.sqlite').read_bytes()).hexdigest() for folder in (folder_a,folder_b)])
        self.assertIn('Passive ambient traffic',(output/'map.html').read_text())

    def test_comparison_rejects_duplicates_unfinished_runs_and_arbitrary_paths(self):
        entry,_,_=self.completed('Ready')
        for selected in ([entry['id'],entry['id']],['../../outside',entry['id']],[entry['id']],[],'not a list'):
            with self.assertRaises(ValueError):
                self.workspace.results.compare(selected)
        empty,_,_=self.completed('Empty',empty=True)
        with self.assertRaisesRegex(ValueError,'mapped mobile'):
            self.workspace.results.compare([entry['id'],empty['id']])

    def test_active_comparison_request_is_reused_and_never_launches_radio_work(self):
        a,_,_=self.completed('A')
        b,_,_=self.completed('B','active')
        first=self.workspace.launch(dict(kind='comparison',sessions=[a['id'],b['id']]))
        second=self.workspace.launch(dict(kind='comparison',sessions=[a['id'],b['id']]))
        self.assertEqual(first['id'],second['id'])
        process=self.workspace.jobs.processes[first['id']]
        self.assertEqual(process.wait(timeout=15),0)
        deadline=time.monotonic()+3
        while self.workspace.jobs.entries[first['id']]['state']=='running' and time.monotonic()<deadline:
            threading.Event().wait(.02)
        description=self.workspace.jobs.describe(first['id'])
        self.assertEqual(description['state'],'completed',description)
        self.assertIn('map.html',description['files'])
        self.assertEqual(self.workspace.launch(dict(kind='comparison',sessions=[a['id'],b['id']]))['id'],first['id'])


if __name__=='__main__':
    unittest.main()
