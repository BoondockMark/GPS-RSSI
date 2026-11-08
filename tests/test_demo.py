"""Synthetic training data stays distinct from measured recordings and service caches."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gps_rssi.cli import parser, run
from gps_rssi.demo import create_demo
from gps_rssi.storage import Store, load_log
from gps_rssi.survey import create_survey
from gps_rssi.training import KIND, descriptor
from gps_rssi.workspace import Workspace


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_demo_builds_replay_recordings_and_a_self_contained_training_report(self):
        output = self.root / 'demo'
        with patch('gps_rssi.elevation.urlopen', side_effect=AssertionError('no elevation requests')):
            summary = create_demo(output)
        self.assertEqual(summary['source'], 'replay')
        self.assertEqual(summary['demo']['kind'], KIND)
        self.assertEqual(summary['selected_samples'],64)
        self.assertEqual(summary['mapped_points'],63)
        sessions, samples, _ = load_log(output/'recordings.sqlite', source='all')
        self.assertEqual(len(sessions),2)
        self.assertEqual({s['source'] for s in sessions},{'replay'})
        self.assertEqual(len(samples),64)
        self.assertTrue(any(s['frequency_hz'] is None for s in samples))
        html = (output/'map.html').read_text(encoding='utf-8')
        self.assertIn('TrainingTerrain',html)
        self.assertIn('Synthetic training',html)
        self.assertNotIn('__TRAINING_JS__',html)
        self.assertIn('Missing frequency',(output/'TEST-SURVEY.txt').read_text())
        self.assertFalse((self.root/'data/.gps-rssi-elevation').exists())

    def test_training_survives_rebuilding_but_mixed_real_data_disables_demo_terrain(self):
        output = self.root/'demo'
        create_demo(output)
        rebuilt = create_survey([output/'recordings.sqlite'],self.root/'rebuilt',source='all')
        self.assertEqual(rebuilt['demo']['kind'],KIND)
        store = Store(self.root/'real.sqlite','Other receiver','mobile','benlink_bluetooth',{})
        store.close()
        mixed = create_survey([output/'recordings.sqlite',self.root/'real.sqlite'],self.root/'mixed',source='all')
        self.assertNotIn('demo',mixed)
        self.assertFalse((self.root/'mixed/TEST-SURVEY.txt').exists())

    def test_normal_replay_and_live_sessions_cannot_accidentally_activate_training(self):
        for sessions in ([],[dict(source='replay',metadata_json='{}')],
                         [dict(source='live',metadata_json=json.dumps(dict(training=KIND)))],
                         [dict(source='replay',metadata_json='invalid')]):
            self.assertIsNone(descriptor(sessions))

    def test_existing_output_is_not_overwritten_and_cli_is_available(self):
        output = self.root/'demo'
        output.mkdir(); (output/'keep.txt').write_text('keep')
        with self.assertRaises(FileExistsError):
            create_demo(output)
        self.assertEqual((output/'keep.txt').read_text(),'keep')
        run(parser().parse_args(['demo','--output',str(self.root/'cli-demo')]))
        self.assertTrue((self.root/'cli-demo/map.html').is_file())

    def test_workspace_demo_needs_no_saved_radio_or_recording(self):
        workspace=Workspace(self.root)
        self.assertEqual(workspace.profiles(),[])
        with patch.object(workspace.jobs,'launch',return_value={'id':'demo'}) as launch:
            workspace.launch(dict(kind='demo'))
            arguments=launch.call_args.args[1](self.root/'result')
            self.assertEqual(arguments,['demo','--output',str(self.root/'result')])
            self.assertEqual(launch.call_args.args[0],'demo')
            self.assertNotIn('profile',launch.call_args.kwargs)
            self.assertFalse(launch.call_args.kwargs.get('resources'))
