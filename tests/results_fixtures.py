"""Synthetic completed sweeps shared by backend and real-browser tests."""

from contextlib import closing
import json

from gps_rssi.session_results import build_results
from gps_rssi.storage import Store


def frame(at, *, rssi=50, locked=True, rx=True):
    return dict(status_received_at=at,status=dict(rssi=rssi,is_in_rx=rx,is_in_tx=False,is_sq=rx,is_gps_locked=locked,curr_ch_id=1),
        channel=dict(channel_id=1,rx_freq=146.52,tx_freq=146.52),
        position=dict(latitude=33.96,longitude=-117.95,time=at,accuracy=8) if locked else None)


class CompletedResultsFixture:
    def key(self):
        self.counter+=1
        return f'{self.counter:032x}'

    def completed(self,name,mode='passive',*,empty=False,recordings=1,profile=None):
        profile=profile or self.profile
        session=self.workspace.sessions.create(dict(name=name,sweep_type=mode,profiles=[profile['id']],notes='Roof antenna'),self.workspace.profiles())
        identities=[]
        for index in range(0 if empty else recordings):
            with closing(Store(self.root/profile['database'],profile['name'],profile['role'],'benlink_bluetooth',dict(sweep_type=mode))) as store:
                identities.append(store.session_id)
                store.sample(frame('2026-11-08T12:00:00Z'))
                store.sample(frame('2026-11-08T12:00:20Z',locked=False))
                store.sample(frame('2026-11-08T12:00:50Z',rssi=60))
                key=self.key()
                folder=self.workspace.jobs.directory/key/'result'
                folder.mkdir(parents=True)
                report=dict(recording_session_id=store.session_id,samples_recorded=3,sweep_type=mode,stop_verified=True)
                (folder/'report.json').write_text(json.dumps(report))
                operation=dict(id=key,kind='record' if mode=='passive' else 'transmit',title=name,profile=profile['id'],
                    survey_session=session['id'],state='stopped',created_at='2026-11-08T12:00:00Z',finished_at='2026-11-08T12:01:00Z',resources=[])
                self.workspace.jobs.entries[key]=operation
                self.workspace.jobs._save(operation)
        stored=self.workspace.sessions.entries[session['id']]
        stored.update(state='finished',finished_at='2026-11-08T12:01:00Z',operations=self.workspace.sessions.operations(session['id']))
        self.workspace.sessions.save(stored)
        result_id=self.key()
        output=self.workspace.jobs.directory/result_id/'result'
        build_results(self.root,session['id'],output)
        self.workspace.jobs.entries[result_id]=dict(id=result_id,kind='session-results',title='Results · '+name,state='completed',
            created_at='2026-11-08T12:01:00Z',resources=[],survey_session=session['id'])
        self.workspace.jobs._save(self.workspace.jobs.entries[result_id])
        stored['result_job']=result_id
        self.workspace.sessions.save(stored)
        return stored,output,identities
