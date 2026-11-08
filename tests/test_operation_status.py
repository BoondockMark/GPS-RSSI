import asyncio
from contextlib import closing
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gps_rssi.app import run_together, start
from gps_rssi.benlink_radio import collect
from gps_rssi.dashboard import make_server
from gps_rssi.operation_status import OperationStatus
from gps_rssi.settings import load_settings
from gps_rssi.storage import Store
from test_logger import FakeConnection


class RecorderStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.settings=load_settings(self.root/'radio.toml')
        self.settings['dashboard']['area']='none'
        self.store=Store(self.root/'radio.sqlite','Vehicle','mobile','benlink_bluetooth',{})
        self.addCleanup(self.store.close)

    async def test_connection_loss_and_retry_have_producer_evidence(self):
        writer=OperationStatus(self.root/'status',samples_recorded=0)
        events=[]
        def observe(**values):
            writer.update(**values)
            events.append(dict(writer.report))
        first=FakeConnection(status_error=TimeoutError('link timed out'))
        second=FakeConnection()
        with patch('gps_rssi.benlink_radio.asyncio.sleep',return_value=None):
            await collect(self.store,iter([first,second]).__next__,gps=True,max_samples=1,
                          connect_timeout=.1,timeout=.1,reconnect_delay=.01,on_status=observe)
        self.assertTrue(first.disconnected)
        self.assertTrue(second.disconnected)
        retry=next(e for e in events if e['state']=='reconnecting' and e.get('retry_at'))
        self.assertEqual(retry['error_stage'],'status')
        self.assertIn('timed out',retry['error'])
        report=json.loads((self.root/'status/report.json').read_text())
        self.assertEqual(report['state'],'recording')
        self.assertEqual(report['attempt'],2)
        self.assertEqual(report['samples_recorded'],1)
        self.assertIsNone(report['error'])
        self.assertTrue(report['latest_telemetry']['gps_recorded'])
        self.assertIn('expires_at',report)

    async def test_initial_status_write_failure_still_closes_dashboard(self):
        server=make_server(self.store.path,port=0,area='none')
        status=Mock(update=Mock(side_effect=OSError('status disk unavailable')))
        with self.assertRaisesRegex(OSError,'status disk unavailable'):
            await run_together(self.store,FakeConnection,server,self.settings,browser=False,status=status)
        self.assertEqual(server.fileno(),-1)

    async def test_status_failure_after_connection_does_not_skip_disconnect(self):
        server=make_server(self.store.path,port=0,area='none')
        conn=FakeConnection()
        status=Mock()
        def update(**values):
            if values.get('state')=='recording':
                raise OSError('status disk unavailable')
            if values.get('state')=='reconnecting':
                raise OSError('status disk unavailable')
        status.update.side_effect=update
        with self.assertRaisesRegex(OSError,'status disk unavailable'):
            await run_together(self.store,lambda:conn,server,self.settings,browser=False,status=status)
        self.assertTrue(conn.disconnected)
        self.assertEqual(server.fileno(),-1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM samples').fetchone()[0],1)


class ManagedStartTests(unittest.TestCase):
    def test_managed_start_retains_identity_and_final_stopped_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=root/'fixed.toml'
            load_settings(config,profile='fixed')
            conn=FakeConnection()
            async def one_sample(*args,**kwargs):
                return await collect(*args,max_samples=1,**kwargs)
            def ephemeral(database,**options):
                options['port']=0
                return make_server(database,**options)
            with patch('gps_rssi.app.create_connection',return_value=conn),patch('gps_rssi.app.make_server',side_effect=ephemeral),patch('gps_rssi.app.collect',side_effect=one_sample):
                start(config,profile='fixed',no_browser=True,status_output=root/'status')
            report=json.loads((root/'status/report.json').read_text())
            self.assertEqual(report['state'],'stopped')
            self.assertEqual(report['samples_recorded'],1)
            self.assertTrue(conn.disconnected)
            with closing(sqlite3.connect(root/'data/fixed.sqlite')) as db:
                self.assertEqual(db.execute('SELECT id FROM sessions').fetchone()[0],report['recording_session_id'])


if __name__=='__main__':
    unittest.main()
