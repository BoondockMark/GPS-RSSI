"""Bounded, read-only live views scoped to managed recording identities."""
from contextlib import closing
import sqlite3

from .measurements import gps_problem, recorded_sweep_type, select_points, timestamp, utc_now
from .sharing import readonly

MAX_RADIOS = 20
MAX_SAMPLES = 1000
MAX_RECORDINGS = 64


def recording_job(job):
    report = job.get('result') or {}
    return job['kind'] == 'record' or (job['kind'] == 'transmit' and
        (report.get('recording') or report.get('recording_session_id') or job['title'].startswith(('Active sweep · ', 'Field survey · '))))


class LiveSurvey:
    def __init__(self, workspace):
        self.workspace = workspace

    def snapshot(self, selected=None):
        workspace = self.workspace
        with workspace.jobs.lock:
            session = workspace.sessions.require(selected) if selected else None
            radios = session['radios'] if session else [p for p in workspace.profiles() if not p.get('error')]
            jobs = [workspace.jobs.describe(key) for key, entry in workspace.jobs.entries.items()
                    if entry['kind'] in ('record', 'transmit') and (not selected or entry.get('survey_session') == selected)]
            if session:
                jobs = [job for job in jobs if job.get('survey_session') == selected]
                known = {job['id'] for job in jobs}
                jobs += [job for job in session.get('operations', []) if job['id'] not in known]
            jobs = [job for job in jobs if recording_job(job)]
            # Producer reports are copied by describe(); no radio or writer is opened.
            rows = []
            for profile in radios[:MAX_RADIOS]:
                own = sorted([job for job in jobs if job.get('profile') == profile['id']], key=lambda job: (job['created_at'], job['id']))
                chosen = own[-1] if own else None
                operations = own if session else own[-1:]
                ids = [(job.get('result') or {}).get('recording_session_id') for job in operations]
                ids = list(dict.fromkeys(key for key in ids if isinstance(key, str) and key))
                job = {key: chosen.get(key) for key in ('id','kind','title','state','created_at','finished_at','message','result')} if chosen else None
                row = dict(profile={key: profile.get(key) for key in ('id','name','role','gps','telemetry_max_age_seconds','connect_timeout_seconds','error')},
                    job=job, records=[], latest=None, latest_fix=None, error=None, truncated=len(ids)>MAX_RECORDINGS,
                    recording_ids=ids[-MAX_RECORDINGS:])
                rows.append((row, profile))
        result = []
        for row, profile in rows:
            if row['recording_ids']:
                try:
                    self._read(row, workspace.inside(workspace.root/profile['database']))
                except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
                    row['error'] = 'Recording unavailable: ' + str(exc)
            # Never use profile.latest: it can belong to an earlier recording.
            row['profile']['latest'] = row['latest']
            result.append(row)
        return dict(created_at=utc_now(), session_id=selected, name=session['name'] if session else 'Current / latest managed runs',
                    radios=result, truncated=len(radios)>MAX_RADIOS, max_radios=MAX_RADIOS, max_samples=MAX_SAMPLES)

    def _read(self, row, path):
        with closing(readonly(path)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            marks = ','.join('?' for _ in row['recording_ids'])
            sessions = [dict(s) for s in db.execute(f"SELECT * FROM sessions WHERE id IN ({marks}) AND source<>'replay'", row['recording_ids'])]
            if not sessions:
                raise ValueError('The managed recording identity is missing')
            if len(sessions) != len(row['recording_ids']):
                row['warning'] = 'Some recording identities are missing; only available measurements are shown'
            ids = [s['id'] for s in sessions]
            marks = ','.join('?' for _ in ids)
            samples = [dict(s) for s in db.execute(f'SELECT * FROM samples WHERE session_id IN ({marks}) ORDER BY id DESC LIMIT ?', [*ids, MAX_SAMPLES+1])]
            row['truncated'] |= len(samples)>MAX_SAMPLES
            samples = list(reversed(samples[:MAX_SAMPLES]))
            events = [dict(e) for e in db.execute(f"SELECT session_id,received_at,kind FROM events WHERE session_id IN ({marks}) AND kind IN ('connected','connection_error','disconnected','disconnect_error','stopped') ORDER BY id DESC LIMIT ?", [*ids, MAX_SAMPLES+1])]
        row['route_breaks_limited'] = len(events)>MAX_SAMPLES
        by_session = {s['id']:s for s in sessions}
        points, _ = select_points(sessions, samples)
        points = {p['id']:p for p in points}
        breaks = {}
        for event in events:
            try:
                breaks.setdefault(event['session_id'], []).append(timestamp(event['received_at']))
            except (ValueError, TypeError, AttributeError):
                continue
        previous = {}
        for sample in samples:
            current = by_session[sample['session_id']]
            mobile = current['role'] == 'mobile'
            located = mobile and not gps_problem(sample)
            try:
                at = timestamp(sample['received_at'])
            except (ValueError, TypeError, AttributeError):
                at = None
                located = False
            last = previous.get(sample['session_id'])
            boundary = len(events)>MAX_SAMPLES or (at is not None and last is not None and any(last<event<=at for event in breaks.get(sample['session_id'], [])))
            previous[sample['session_id']] = at
            point = points.get(sample['id'])
            record = dict(id=sample['id'], session=sample['session_id'], time=sample['received_at'],
                position_time=sample['position_time'], lat=sample['latitude'] if located else None,
                lon=sample['longitude'] if located else None, break_before=bool(boundary),
                rx=bool(sample['is_in_rx']), tx=bool(sample['is_in_tx']),
                rssi=point['rssi'] if point else None, sweep_type=recorded_sweep_type(current['metadata_json']),
                frequency_hz=sample.get('frequency_hz'), gps_locked=bool(sample['gps_locked']))
            row['records'].append(record)
            if located:
                row['latest_fix'] = record
            row['latest'] = {key: sample[key] for key in ('received_at','rssi_value','rssi_unit','is_in_rx','is_in_tx','gps_locked')}
            row['latest']['recording_session_id'] = sample['session_id']
        if samples:
            latest = samples[-1]
            row['gps_reason'] = 'fixed_station' if by_session[latest['session_id']]['role'] != 'mobile' else gps_problem(latest)
        row['sweep_types'] = sorted({recorded_sweep_type(s['metadata_json']) for s in sessions})
