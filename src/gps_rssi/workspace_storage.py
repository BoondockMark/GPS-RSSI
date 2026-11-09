"""On-demand storage inventory and explicit cleanup of owned, unreferenced files."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time

from .local_dem import LocalDEM
from .measurements import utc_now
from .workspace_jobs import ACTIVE, read_json
from .storage_policy import UPLOAD_LIMITS

DERIVED = {'preview', 'analyze', 'survey', 'merge', 'export', 'share', 'demo', 'offline-export', 'comparison'}


def tree_info(path):
    """Never follow symlinks, including a redirected parent directory."""
    path = Path(path)
    entries, size = [], 0
    if not path.exists():
        return dict(bytes=0, revision='', safe=False)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        return dict(bytes=0, revision='', safe=False)
    def visit(item):
        nonlocal size
        if item.is_symlink():
            raise ValueError('Linked files are protected')
        stat = item.stat()
        entries.append((str(item.relative_to(path.parent)), stat.st_size, stat.st_mtime_ns))
        if item.is_file():
            size += stat.st_size
        elif item.is_dir():
            for child in sorted(item.iterdir()):
                visit(child)
        else:
            raise ValueError('Special files are protected')
    try:
        visit(path)
    except (OSError, ValueError):
        return dict(bytes=size, revision='', safe=False)
    return dict(bytes=size, revision=hashlib.sha256(json.dumps(entries).encode()).hexdigest(), safe=True)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class WorkspaceStorage:
    def __init__(self, workspace):
        self.workspace = workspace
        self.lock = threading.Lock()

    def inventory(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('A file import or cleanup is in progress. Refresh after it finishes')
        try:
            with self.workspace.jobs.lock:
                return self._inventory()
        finally:
            self.lock.release()

    def _inventory(self):
        workspace, rows = self.workspace, []
        root = workspace.root
        busy = (any(entry['state'] in ACTIVE for entry in workspace.jobs.entries.values())
                or any(entry['state'] == 'finishing' for entry in workspace.sessions.entries.values()))
        blocked_reason = 'Finish or acknowledge all managed operations before cleanup' if busy else ''
        references = []
        protected_jobs = set()
        for session in workspace.sessions.entries.values():
            for radio in session.get('radios', []):
                references.append(((root/radio['database']).resolve(), 'named sweep '+session['name']))
            if session.get('result_job'):
                protected_jobs.add(session['result_job'])
            protected_jobs.update(op['id'] for op in session.get('operations', []))
        for profile in workspace.profiles():
            if not profile.get('error'):
                references.append(((root/profile['database']).resolve(), 'radio setup '+profile['name']))
        for key, job in workspace.jobs.entries.items():
            if job.get('storage_removed_at'):
                continue
            for source in job.get('comparison_sources', []):
                protected_jobs.add(source['result_job'])
            inputs = list(strings(job.get('input_paths', [])))
            summary = workspace.jobs.folder(key) / 'result' / 'summary.json'
            if summary.is_file():
                try:
                    inputs.extend(strings(read_json(summary).get('inputs', [])))
                except (OSError, ValueError, TypeError, AttributeError):
                    # Unknown dependencies: fail closed instead of guessing.
                    busy = True
                    blocked_reason = 'Cannot read dependencies for '+job['title']+'. Restore its summary before cleanup'
            for value in inputs:
                path = (root / value).resolve()
                if path.is_relative_to(root):
                    references.append((path, job['title']))
        def append(identity, name, category, path, reason=None):
            info = tree_info(path)
            if not info['safe']:
                reason = 'Linked, unreadable, or changed files are protected'
            for source, title in references:
                if source == path.resolve() or source.is_relative_to(path.resolve()):
                    reason = 'Referenced by ' + title
                    break
            rows.append(dict(id=identity, name=name, category=category, path=str(path.relative_to(root)),
                bytes=info['bytes'], revision=info['revision'], eligible=not reason and not busy,
                reason=reason or (blocked_reason if busy else 'Available for permanent deletion')))
        directory = root / 'data/workspace/imports'
        for path in sorted(directory.glob('*')):
            if not re.fullmatch('[0-9a-f]{32}', path.name) or not path.is_dir():
                continue
            try:
                name = read_json(path/'import.json')['name']
                reason = None
            except (OSError, ValueError, KeyError):
                name, reason = 'Incomplete import · ' + path.name[:8], None
            append('import:'+path.name, name, 'Imports', path, reason)
        for path in sorted(directory.glob('.upload-*')) + sorted(directory.glob('.import-*')):
            if time.time()-path.stat().st_mtime >= 86400:
                append('temporary:'+path.name, 'Interrupted import · '+path.name, 'Temporary files', path)
        for path in sorted((root/'data').glob('.offline-work-*')):
            if time.time()-path.stat().st_mtime >= 86400:
                append('temporary:'+path.name, 'Interrupted offline work · '+path.name, 'Temporary files', path)
        for key, job in workspace.jobs.entries.items():
            path = workspace.jobs.folder(key) / 'result'
            if not path.exists():
                continue
            reason = ('Named sweep or comparison source' if key in protected_jobs or job.get('survey_session') else
                      'Original recording, capture, or diagnostic evidence' if job['kind'] not in DERIVED else None)
            append('job:'+key, job['title'], 'Results', path, reason)
        terrain = LocalDEM(root/'data')
        try:
            current = (terrain.config() or {}).get('file')
        except (OSError, ValueError):
            current = None
            busy = True
            blocked_reason = 'Cannot read the current terrain manifest. Restore it before cleanup'
        for path in sorted(terrain.directory.glob('*.tif')):
            if not re.fullmatch(r'[0-9a-f]{32}\.tif', path.name):
                continue
            reason = 'Current terrain raster' if path.name == current else ('Recent terrain: retained for in-flight readers for 60 seconds' if time.time()-path.stat().st_mtime < 60 else None)
            append('terrain:'+path.stem, 'Terrain · '+path.stem[:8], 'Terrain', path, reason)
        if busy:
            for row in rows:
                row['eligible'] = False
                if row['reason'] == 'Available for permanent deletion':
                    row['reason'] = blocked_reason
        categories = []
        for name, path in [('Recordings and backups', root/'data'), ('Workspace imports', directory),
                           ('Workspace results', root/'data/workspace/jobs'), ('Named sweep manifests', root/'data/workspace/sessions'),
                           ('Terrain rasters', terrain.directory), ('Offline maps', root/'data/.gps-rssi-offline'),
                           ('Map cache', root/'data/.gps-rssi-tiles'), ('Elevation cache', root/'data/.gps-rssi-elevation')]:
            info = tree_info(path)
            categories.append(dict(name=name, path=str(path.relative_to(root)), bytes=info['bytes']))
        # The first category is the total data tree; show it separately to avoid double counting.
        total = categories.pop(0)['bytes']
        managed = sum(item['bytes'] for item in categories)
        categories.insert(0, dict(name='Recordings, backups, and other data', path='data', bytes=max(0,total-managed)))
        disk = shutil.disk_usage(root)
        return dict(data_bytes=total, free_bytes=disk.free, disk_bytes=disk.total, categories=categories, items=rows,
                    cleanup_blocked=busy, cleanup_reason=blocked_reason, upload_limits=UPLOAD_LIMITS)

    def remove(self, identity, revision, confirmed):
        if confirmed is not True:
            raise ValueError('Confirm permanent deletion of this item')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('An import or cleanup is in progress')
        try:
            with self.workspace.jobs.lock:
                return self._remove(identity, revision)
        finally:
            self.lock.release()

    def _remove(self, identity, revision):
        catalog = self._inventory()
        row = next((row for row in catalog['items'] if row['id'] == identity), None)
        if row is None:
            raise ValueError('Choose an existing storage item')
        if not row['eligible']:
            raise ValueError(row['reason'])
        if not revision or row['revision'] != revision:
            raise ValueError('This item changed. Refresh storage and review it again')
        path = self.workspace.root / row['path']
        # Enumeration supplies the path; clients never supply filesystem paths.
        if not path.resolve().is_relative_to(self.workspace.root) or not tree_info(path)['safe']:
            raise ValueError('Storage item is outside managed storage')
        if row['category'] == 'Results':
            key = identity.split(':')[1]
            entry = self.workspace.jobs.entries[key]
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        if row['category'] == 'Results':
            entry['storage_removed_at'] = utc_now()
            self.workspace.jobs._save(entry)
            self.workspace.jobs.result_summaries.pop(key, None)
        return dict(removed=identity, bytes=row['bytes'])
