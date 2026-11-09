from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from gps_rssi.workspace import Workspace
from gps_rssi.storage import Store
from gps_rssi.offline import OfflineData
from gps_rssi.storage_policy import UPLOAD_LIMITS, required_space, check_space
from gps_rssi.storage import load_log
from results_fixtures import CompletedResultsFixture, frame


class WorkspaceStorageTests(CompletedResultsFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.profile = self.workspace.save_profile(dict(name='Vehicle', role='mobile', address='AA:BB:CC:DD:EE:01', gps=True))
        self.counter = 0

    def imported(self):
        source = self.root/'source.sqlite'
        if not source.exists():
            with closing(Store(source, 'Vehicle', 'mobile', 'benlink_bluetooth', {})):
                pass
        self.workspace.register_import(source, 'database', 'Shared recording')
        self.assertTrue(source.is_file())
        return next(row for row in self.workspace.storage.inventory()['items'] if row['category']=='Imports')

    def job(self, key='a'*32, kind='survey', **extra):
        jobs = self.workspace.jobs
        folder = jobs.directory/key/'result'
        folder.mkdir(parents=True)
        (folder/'map.html').write_text('Synthetic result')
        jobs.entries[key] = dict(id=key,kind=kind,title='Synthetic result',state='completed',resources=[],**extra)
        jobs._save(jobs.entries[key])
        return folder

    def remove(self, row):
        return self.workspace.storage.remove(row['id'],row['revision'],True)

    def test_import_failure_does_not_publish_or_move_source(self):
        source = self.root/'source.sqlite'
        with closing(Store(source, 'Vehicle', 'mobile', 'benlink_bluetooth', {})): pass
        original = source.read_bytes()
        write = Path.write_text
        def fail(path, *args, **kwargs):
            if path.name=='import.json': raise OSError('disk full')
            return write(path,*args,**kwargs)
        with patch.object(Path,'write_text',fail), self.assertRaises(OSError):
            self.workspace.register_import(source,'database','Shared recording')
        self.assertEqual(source.read_bytes(),original)
        self.assertFalse(list((self.root/'data/workspace/imports').iterdir()))

    def test_import_of_open_database_uses_snapshot_with_committed_wal(self):
        source=self.root/'live.sqlite'
        with closing(Store(source,'Vehicle','mobile','benlink_bluetooth',{})) as store:
            store.sample(frame('2026-11-08T12:00:00Z'))
            self.workspace.register_import(source,'database','Open recording')
            dataset=next(d for d in self.workspace.datasets()['databases'] if d['source']=='import')
            self.assertEqual(len(load_log(self.root/dataset['path'])[1]),1)
            self.assertTrue(source.exists())

    def test_publication_failure_cleans_staging_and_preserves_source(self):
        source=self.root/'source.sqlite'
        with closing(Store(source,'Vehicle','mobile','benlink_bluetooth',{})):pass
        with patch.object(Path,'rename',side_effect=OSError('publication failed')),self.assertRaises(OSError):
            self.workspace.register_import(source,'database','Recording')
        self.assertTrue(source.exists())
        self.assertFalse(list((self.root/'data/workspace/imports').iterdir()))

    def test_failed_export_removes_only_new_partial_output(self):
        target = self.root/'offline.zip'
        with patch.object(zipfile.ZipFile,'write',side_effect=OSError('disk full')), self.assertRaises(OSError):
            OfflineData(self.root/'data').export_pack(target)
        self.assertFalse(target.exists())
        target.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            OfflineData(self.root/'data').export_pack(target)
        self.assertEqual(target.read_bytes(),b'keep')

    def test_offline_export_closes_sqlite_handles_for_windows_cleanup(self):
        opened=[]
        connect=sqlite3.connect
        class TrackedConnection(sqlite3.Connection):
            closed=False
            def close(self):
                self.closed=True
                return super().close()
        def tracked(*args,**kwargs):
            db=connect(*args,**kwargs,factory=TrackedConnection)
            opened.append(db)
            return db
        with patch('gps_rssi.offline.sqlite3.connect',side_effect=tracked):
            OfflineData(self.root/'data').export_pack(self.root/'offline.zip')
        self.assertTrue(opened)
        self.assertTrue(all(db.closed for db in opened))

    def test_unused_import_requires_confirmation_and_exact_review_revision(self):
        row = self.imported()
        self.assertTrue(row['eligible'])
        with self.assertRaises(ValueError):self.workspace.storage.remove(row['id'],row['revision'],False)
        data = self.root/row['path']/'data.sqlite'
        os.utime(data, ns=(data.stat().st_atime_ns,data.stat().st_mtime_ns+1000000))
        with self.assertRaisesRegex(ValueError,'changed'):self.remove(row)
        fresh = self.workspace.storage.inventory()['items'][0]
        result = self.remove(fresh)
        self.assertGreater(result['bytes'],0)
        self.assertFalse((self.root/row['path']).exists())
        self.assertTrue((self.root/'source.sqlite').exists())

    def test_completed_results_protect_inputs_until_result_removed(self):
        row = self.imported()
        self.job(input_paths=[row['path']+'/data.sqlite'])
        items = {r['id']:r for r in self.workspace.storage.inventory()['items']}
        self.assertFalse(items[row['id']]['eligible'])
        self.assertIn('Referenced',items[row['id']]['reason'])
        self.remove(items['job:'+'a'*32])
        self.assertTrue(self.workspace.storage.inventory()['items'][0]['eligible'])
        self.assertIn('a'*32,self.workspace.jobs.entries)

    def test_new_or_unknown_operation_blocks_previously_reviewed_cleanup(self):
        row = self.imported()
        for state in ('running','stopping','unknown'):
            self.workspace.jobs.entries['b'*32]=dict(id='b'*32,kind='survey',title='Reading import',state=state)
            with self.assertRaises(ValueError):self.remove(row)
            self.assertTrue((self.root/row['path']).exists())

    def test_named_sweep_and_comparison_sources_and_captures_are_protected(self):
        session,_,_=self.completed('Named sweep')
        self.job(kind='capture')
        self.job(key='b'*32)
        self.job(key='c'*32,kind='comparison',comparison_sources=[dict(result_job='b'*32)])
        items = {r['id']:r for r in self.workspace.storage.inventory()['items']}
        for key in (session['result_job'],'a'*32,'b'*32):
            self.assertFalse(items['job:'+key]['eligible'])
            with self.assertRaises(ValueError):self.remove(items['job:'+key])
        self.assertTrue(items['job:'+'c'*32]['eligible'])

    def test_summary_dependencies_from_previous_app_versions_are_protected(self):
        row = self.imported()
        folder = self.job()
        (folder/'summary.json').write_text(json.dumps(dict(inputs=[row['path']+'/data.sqlite'])))
        self.assertFalse(self.workspace.storage.inventory()['items'][0]['eligible'])
        (folder/'summary.json').write_text('broken')
        self.assertTrue(self.workspace.storage.inventory()['cleanup_blocked'])

    def test_current_and_recent_terrain_protected_unused_terrain_reclaims_space(self):
        folder = self.root/'data/.gps-rssi-dem'
        folder.mkdir(parents=True)
        current,old,recent = (folder/(key*32+'.tif') for key in ('a','b','c'))
        for path in (current,old,recent):path.write_bytes(b'fixture')
        os.utime(old,(time.time()-3600,)*2)
        (folder/'current.json').write_text(json.dumps(dict(file=current.name)))
        items = {r['id']:r for r in self.workspace.storage.inventory()['items']}
        self.assertFalse(items['terrain:'+'a'*32]['eligible'])
        self.assertFalse(items['terrain:'+'c'*32]['eligible'])
        self.remove(items['terrain:'+'b'*32])
        self.assertFalse(old.exists());self.assertTrue(current.exists())

    def test_linked_storage_and_arbitrary_identifiers_cannot_delete_external_files(self):
        row = self.imported()
        outside = self.root/'keep.txt';outside.write_text('keep')
        try:(self.root/row['path']/'link').symlink_to(outside)
        except OSError:self.skipTest('Symlinks unavailable')
        item = self.workspace.storage.inventory()['items'][0]
        self.assertFalse(item['eligible'])
        for identity in ('../../keep.txt', 'import:../../keep.txt', row['id']):
            with self.assertRaises(ValueError):self.workspace.storage.remove(identity,row['revision'],True)
        self.assertEqual(outside.read_text(),'keep')

    def test_crashed_uploads_require_age_and_explicit_cleanup(self):
        folder = self.root/'data/workspace/imports';folder.mkdir(parents=True)
        old = folder/'.upload-old';old.write_bytes(b'partial')
        new = folder/'.upload-current';new.write_bytes(b'partial')
        os.utime(old,(time.time()-90000,)*2)
        rows = self.workspace.storage.inventory()['items']
        self.assertEqual([r['id'] for r in rows],['temporary:.upload-old'])
        self.remove(rows[0]);self.assertTrue(new.exists())

    def test_concurrent_import_blocks_inventory_and_cleanup(self):
        row = self.imported()
        with self.workspace.storage.lock:
            with self.assertRaises(RuntimeError):self.workspace.storage.inventory()
            with self.assertRaises(RuntimeError):self.remove(row)

    def test_limits_and_working_space_allow_expanded_pack(self):
        self.assertEqual(UPLOAD_LIMITS['map'],1024**3)
        self.assertEqual(UPLOAD_LIMITS['audio'],128*1024**2)
        self.assertGreater(required_space('pack',1),3*1024**3)
        usage = type('Usage',(),{'free':1})()
        with patch('gps_rssi.storage_policy.shutil.disk_usage',return_value=usage),self.assertRaisesRegex(ValueError,'free space'):
            check_space(self.root,required_space('map',100))
