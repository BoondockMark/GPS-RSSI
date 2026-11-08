import asyncio
from contextlib import closing
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gps_rssi.app import start
from gps_rssi.benlink_radio import collect
from gps_rssi.controls import Controls
from gps_rssi.dashboard import make_server
from gps_rssi.field_check import run_check
from gps_rssi.field_recording import field_recording
from gps_rssi.measurements import utc_now
from gps_rssi.session_results import build_results
from gps_rssi.storage import Store, export_csv, load_log
from gps_rssi.survey import survey_data
from gps_rssi.survey_sessions import session_path
from gps_rssi.workspace import Workspace
from test_app import LiveFakeConnection
from test_logger import FRAME


class SweepTypeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=Workspace(self.root)
        self.profile=self.workspace.save_profile(dict(name='Vehicle',role='mobile',gps=True,address='AA:BB:CC:DD:EE:01'))

    def session(self,mode):
        return self.workspace.sessions.create(dict(name=mode.title()+' run',sweep_type=mode,profiles=[self.profile['id']],notes='Roof antenna'),self.workspace.profiles())

    def preflight(self,mode,session=None):
        key=f'{len(self.workspace.jobs.entries)+1:032x}'
        folder=self.workspace.jobs.directory/key/'result'
        folder.mkdir(parents=True)
        report=dict(sweep_type=mode,ready=True,profile_revision=self.profile['revision'],checked_at=utc_now())
        (folder/'report.json').write_text(json.dumps(report))
        self.workspace.jobs.entries[key]=dict(id=key,kind='preflight',profile=self.profile['id'],state='completed',
            title=mode.title()+' preflight',survey_session=session,created_at=utc_now(),resources=[])

    def test_modes_persist_and_legacy_manifests_remain_active(self):
        passive=self.session('passive')
        self.assertEqual(Workspace(self.root).sessions.describe(passive['id'])['sweep_type'],'passive')
        active=self.session('active')
        path=session_path(self.root,active['id'])
        saved=json.loads(path.read_text())
        saved.pop('sweep_type')
        path.write_text(json.dumps(saved))
        self.assertEqual(Workspace(self.root).sessions.describe(active['id'])['sweep_type'],'active')
        with self.assertRaises(ValueError):
            self.session('other')

    def test_passive_requests_never_prepare_tone_transmission(self):
        entry=self.session('passive')
        with patch.object(self.workspace.jobs,'launch') as launch:
            for data in (dict(profile=self.profile['id'],sweep_type='passive'),
                         dict(profile=self.profile['id'],sweep_type='active',survey_session=entry['id'],field=True,automatic=True)):
                with self.assertRaisesRegex(ValueError,'[Pp]assive'):
                    self.workspace.prepare_transmit(data)
            launch.assert_not_called()

    def test_passive_launch_uses_only_the_readonly_recorder_and_mode_matching_check(self):
        entry=self.session('passive')
        payload=dict(kind='record',field=True,sweep_type='passive',profile=self.profile['id'],survey_session=entry['id'])
        self.preflight('active',entry['id'])
        with self.assertRaisesRegex(ValueError,'passive preflight'):
            self.workspace.launch(payload)
        self.preflight('passive',entry['id'])
        with patch.object(self.workspace.jobs,'launch',return_value={}) as launch:
            self.workspace.launch(payload)
        args=launch.call_args.args[1](self.root/'result')
        self.assertEqual(args[0],'start')
        self.assertNotIn('--transmit',args)
        self.assertNotIn('--automatic',args)
        self.assertNotIn('tone',args)
        self.assertIn('--survey-session',args)
        self.assertEqual(launch.call_args.kwargs['survey_session'],entry['id'])

    def test_active_session_rejects_a_passive_recorder(self):
        entry=self.session('active')
        with patch.object(self.workspace.jobs,'launch') as launch:
            with self.assertRaisesRegex(ValueError,'active sweep'):
                self.workspace.launch(dict(kind='record',field=True,sweep_type='passive',profile=self.profile['id'],survey_session=entry['id']))
            launch.assert_not_called()

    def test_preflight_type_cannot_be_changed_for_a_named_session(self):
        entry=self.session('passive')
        with patch.object(self.workspace.jobs,'launch') as launch:
            with self.assertRaisesRegex(ValueError,'passive sweep'):
                self.workspace.launch(dict(kind='preflight',sweep_type='active',profile=self.profile['id'],survey_session=entry['id']))
            self.workspace.launch(dict(kind='preflight',profile=self.profile['id'],survey_session=entry['id']))
        args=launch.call_args.args[1](self.root/'result')
        self.assertEqual(args[-2:],['--sweep-type','passive'])

    def test_passive_preflight_needs_no_audio_tools_discovery_or_audio_stream(self):
        conn=LiveFakeConnection()
        with patch('gps_rssi.field_check.create_connection',return_value=conn), \
             patch('gps_rssi.field_check.tone_pcm',side_effect=AssertionError('no tone')), \
             patch('gps_rssi.field_check.encode_sbc',side_effect=AssertionError('no codec')), \
             patch('gps_rssi.field_check.discover_services',side_effect=AssertionError('no audio discovery')), \
             patch('gps_rssi.field_check.new_stream',side_effect=AssertionError('no audio stream')), \
             patch('gps_rssi.field_check.audio_lock',side_effect=AssertionError('no Classic address required')):
            report=run_check(self.root/'check',self.root/self.profile['id'],'passive')
        self.assertTrue(report['ready'],report)
        self.assertEqual(report['sweep_type'],'passive')
        self.assertEqual(conn.calls,['connect','get_status','get_channel'])
        self.assertTrue(conn.disconnected)

    def test_active_preflight_still_requires_the_tone_encoder(self):
        with patch('gps_rssi.field_check.tone_pcm',side_effect=RuntimeError('Audio codecs missing')),patch('gps_rssi.field_check.create_connection') as connect:
            report=run_check(self.root/'active-check',self.root/self.profile['id'],'active')
        self.assertFalse(report['ready'])
        self.assertIn('Audio codecs missing',report['checks'][-1]['detail'])
        connect.assert_not_called()

    def test_passive_recording_cannot_be_attached_to_the_tone_writer(self):
        entry=self.session('passive')
        with self.assertRaisesRegex(ValueError,'passive sweep'):
            with field_recording(self.root/self.profile['id'],self.profile['address'],'mobile',{},session_path(self.root,entry['id'])):
                self.fail('Passive session opened a tone writer')
        self.assertFalse((self.root/self.profile['database']).exists())

    def test_passive_start_and_results_retain_mode_and_recording_identity(self):
        entry=self.session('passive')
        config=self.root/self.profile['id']
        conn=LiveFakeConnection()
        servers=[]
        async def one_sample(*args,**kwargs):
            self.assertFalse(servers[0].controls.allow_reset)
            return await collect(*args,max_samples=1,**kwargs)
        def ephemeral(database,**options):
            options['port']=0
            server=make_server(database,**options)
            servers.append(server)
            return server
        with patch('gps_rssi.app.create_connection',return_value=conn),patch('gps_rssi.app.make_server',side_effect=ephemeral), \
             patch('gps_rssi.app.collect',side_effect=one_sample),patch('gps_rssi.audio_signal.dependencies',side_effect=AssertionError('no codecs')):
            start(config,no_browser=True,status_output=self.root/'status',survey_session=session_path(self.root,entry['id']))
        report=json.loads((self.root/'status/report.json').read_text())
        self.assertEqual(report['sweep_type'],'passive')
        self.assertEqual(report['state'],'stopped')
        self.assertEqual(report['survey_session_id'],entry['id'])
        sessions,samples,_=load_log(self.root/self.profile['database'])
        metadata=json.loads(sessions[0]['metadata_json'])
        self.assertEqual(metadata['sweep_type'],'passive')
        self.assertEqual(metadata['survey_session']['sweep_type'],'passive')
        self.assertEqual(sessions[0]['id'],report['recording_session_id'])
        self.assertEqual(len(samples),1)
        manifest=self.workspace.sessions.entries[entry['id']]
        manifest.update(state='finished',finished_at=utc_now(),operations=[dict(id='x',kind='record',profile=self.profile['id'],
            title='Passive sweep',state='stopped',result=report,message='Stopped')])
        self.workspace.sessions.save(manifest)
        output=self.root/'results'
        summary=build_results(self.root,entry['id'],output)
        self.assertEqual(summary['sweep_type'],'passive')
        self.assertIn('PASSIVE SWEEP RESULTS',(output/'index.html').read_text())
        self.assertNotIn('Completed tones',(output/'index.html').read_text())
        with (output/'measurements.csv').open() as source:
            self.assertEqual(next(csv.DictReader(source))['sweep_type'],'passive')
        geojson=json.loads((output/'measurements.geojson').read_text())
        self.assertEqual(geojson['features'][0]['properties']['sweep_type'],'passive')
        self.assertTrue(conn.disconnected)

    def test_unknown_old_recordings_are_not_mislabeled_passive(self):
        database=self.root/'old.sqlite'
        with closing(Store(database,'Old run','mobile','replay',{})) as store:
            store.sample(FRAME)
        _,observations,summary=survey_data(database,source='all')
        self.assertEqual(summary['sweep_type'],'unclassified')
        self.assertEqual(observations[0]['sweep_type'],'unclassified')
        export_csv(database,self.root/'old.csv')
        with (self.root/'old.csv').open() as source:
            self.assertEqual(next(csv.DictReader(source))['sweep_type'],'unclassified')


class SweepControlsTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_sweep_cannot_reset_away_its_recording_identity(self):
        controls=Controls(asyncio.get_running_loop(),allow_reset=False)
        with self.assertRaisesRegex(RuntimeError,'named sweep'):
            controls.submit('reset')
        controls.submit('exit')
        self.assertEqual((await asyncio.wait_for(controls.queue.get(),1))['action'],'exit')


if __name__=='__main__':
    unittest.main()
