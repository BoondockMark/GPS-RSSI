import http.client
import io
import json
import re
import socket
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from gps_rssi.storage import Store
from gps_rssi.workspace_server import make_workspace_server, OSM_TEMPLATE
from gps_rssi.tiles import Tile, PNG


class WorkspaceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = make_workspace_server(self.root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.request("GET", "/api/state")[1]["token"]
        self.profile = self.server.workspace.save_profile(dict(name="Vehicle", role="mobile", address="AA:BB:CC:DD:EE:01", gps=True))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temp.cleanup()

    def request(self, method, route, data=None, *, auth=False, headers=None, raw=False):
        supplied = {"Content-Type": "application/json"}
        if auth:
            supplied["X-GPS-RSSI-Workspace"] = self.token
        supplied.update(headers or {})
        body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, route, body=body, headers=supplied)
            response = connection.getresponse()
            payload = response.read()
            return response.status, payload if raw else json.loads(payload)
        finally:
            connection.close()

    def test_root_assets_and_state_work_without_existing_recordings(self):
        for path in ("/", "/assets/workspace.js", "/assets/workspace.css"):
            code, body = self.request("GET", path, raw=True)
            self.assertEqual(code, 200)
            self.assertTrue(body)
        code, state = self.request("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(state["profiles"][0]["name"], "Vehicle")

    def test_wrong_host_origin_and_missing_token_cannot_read_or_mutate(self):
        with patch.object(self.server.workspace, "launch") as launch:
            self.assertEqual(self.request("POST", "/api/jobs", dict(kind="preview"))[0], 403)
            self.assertEqual(self.request("POST", "/api/jobs", dict(kind="preview"), auth=True,
                                         headers={"Origin": "https://example.com"})[0], 403)
            self.assertEqual(self.request("GET", "/api/state", headers={"Host": "attacker.example"})[0], 403)
            self.assertEqual(self.request("GET", "/api/state", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
            launch.assert_not_called()

    def test_named_sessions_require_authenticated_creation_and_valid_members(self):
        data=dict(name='Ridge test',notes='Roof antenna',profiles=[self.profile['id']])
        self.assertEqual(self.request('POST','/api/sessions',data)[0],403)
        code,entry=self.request('POST','/api/sessions',data,auth=True)
        self.assertEqual(code,201)
        self.assertEqual(entry['state'],'open')
        self.assertFalse(self.server.workspace.jobs.entries)
        self.assertEqual(self.request('POST','/api/sessions/finish',dict(id='../../outside'),auth=True)[0],400)
        self.assertEqual(self.request('GET','/api/state')[1]['sessions'][0]['name'],'Ridge test')

    def test_results_catalog_is_read_only_and_comparison_requires_valid_authenticated_sources(self):
        self.assertEqual(self.request('GET', '/api/results'), (200, dict(results=[], comparisons=[])))
        self.assertEqual(self.request('GET', '/api/results', headers={'Sec-Fetch-Site':'cross-site'})[0], 403)
        self.assertEqual(self.request('GET', '/assets/results-model.js', raw=True)[0], 200)
        data = dict(kind='comparison', sessions=['a'*32, 'b'*32])
        self.assertEqual(self.request('POST', '/api/jobs', data)[0], 403)
        with patch.object(self.server.workspace.jobs, 'launch') as launch:
            for selected in (None, [], ['a'*32]*2, ['../../outside','b'*32], ['a'*32,'b'*32]):
                self.assertEqual(self.request('POST', '/api/jobs', dict(kind='comparison',sessions=selected), auth=True)[0], 400)
            launch.assert_not_called()

    def test_overview_file_capability_is_read_only_and_scoped_to_one_job(self):
        jobs=self.server.workspace.jobs
        for key in ('a'*32,'b'*32):
            folder=jobs.directory/key/'result'
            folder.mkdir(parents=True)
            jobs.entries[key]=dict(id=key,kind='session-results',title='Survey',state='completed',created_at='2026-11-08T00:00:00Z')
            (folder/'index.html').write_text('<a href="map.html">Open map</a>')
            (folder/'map.html').write_text('<html>Run map</html>')
        code,body=self.request('GET','/files/'+'a'*32+'/index.html',raw=True)
        self.assertEqual(code,200)
        capability=re.search(rb'file_token=([0-9a-f]+)',body)[1].decode()
        headers={'Origin':'null','Sec-Fetch-Site':'cross-site'}
        route='/files/'+'a'*32+'/map.html?file_token='+capability
        self.assertEqual(self.request('GET',route,headers=headers,raw=True)[0],200)
        self.assertEqual(self.request('GET','/files/'+'b'*32+'/map.html?file_token='+capability,headers=headers)[0],403)
        self.assertEqual(self.request('GET','/api/state?file_token='+capability,headers=headers)[0],403)
        self.assertEqual(self.request('POST','/api/sessions?file_token='+capability,{},headers=headers)[0],403)

    def test_transmission_cannot_start_from_unreviewed_request(self):
        with patch.object(self.server.workspace.jobs, "launch") as launch:
            code, _ = self.request("POST", "/api/jobs", dict(kind="transmit", profile=self.profile["id"], confirmed=True), auth=True)
            self.assertEqual(code, 400)
            code, review = self.request("POST", "/api/prepare-transmit", dict(profile=self.profile["id"]), auth=True)
            self.assertEqual(code, 200)
            self.assertIn("intent", review)
            launch.assert_not_called()

    def test_invalid_uploads_are_removed_and_real_recordings_are_imported(self):
        headers={"Content-Type":"application/octet-stream", "X-File-Name":"bad.sqlite"}
        self.assertEqual(self.request("POST", "/api/import?kind=database", b"not sqlite", auth=True, headers=headers)[0], 400)
        directory = self.root / "data/workspace/imports"
        self.assertFalse(list(directory.glob(".upload-*")))
        source = self.root / "source.sqlite"
        store = Store(source, "other-mobile", "mobile", "replay", {})
        store.close()
        headers["X-File-Name"]="shared.sqlite"
        code, datasets=self.request("POST", "/api/import?kind=database", source.read_bytes(), auth=True, headers=headers)
        self.assertEqual(code,201)
        self.assertEqual(datasets["databases"][0]["name"], "shared.sqlite")

    def test_upload_limits_are_type_specific_and_reject_before_reading(self):
        headers={'Content-Type':'application/octet-stream','Content-Length':str(128*1024**2+1)}
        self.assertEqual(self.request('POST','/api/import?kind=database',b'',auth=True,headers=headers)[0],413)
        self.assertEqual(self.request('GET','/api/state')[1]['upload_limits']['terrain'],1024**3)
        with patch('gps_rssi.workspace_server.UPLOAD_LIMITS',dict(database=3,map=4)), patch.object(self.server.workspace,'register_import',return_value={}) as register:
            headers.pop('Content-Length')
            self.assertEqual(self.request('POST','/api/import?kind=database',b'123',auth=True,headers=headers)[0],201)
            self.assertEqual(self.request('POST','/api/import?kind=database',b'1234',auth=True,headers=headers)[0],413)
            self.assertEqual(register.call_count,1)

    def test_low_space_and_concurrent_import_are_rejected_without_staging_files(self):
        headers={'Content-Type':'application/octet-stream'}
        with patch('gps_rssi.workspace_server.check_space',side_effect=ValueError('Not enough free space')):
            self.assertEqual(self.request('POST','/api/import?kind=database',b'123',auth=True,headers=headers)[0],400)
        with self.server.workspace.storage.lock:
            self.assertEqual(self.request('POST','/api/import?kind=database',b'123',auth=True,headers=headers)[0],409)
            self.assertEqual(self.request('GET','/api/storage')[0],409)
        self.assertFalse(list((self.root/'data/workspace/imports').glob('.upload-*')))

    def test_interrupted_upload_closes_before_unlink_and_never_publishes(self):
        import tempfile as temporary_files
        opened=[]
        original=temporary_files.NamedTemporaryFile
        def tracked(*args,**kwargs):
            stream=original(*args,**kwargs);opened.append(stream);return stream
        unlink=Path.unlink
        def windows_unlink(path,*args,**kwargs):
            if path.name.startswith('.upload-'):
                self.assertTrue(all(stream.closed for stream in opened),'Windows cannot unlink an open upload')
            return unlink(path,*args,**kwargs)
        with patch('gps_rssi.workspace_server.tempfile.NamedTemporaryFile',side_effect=tracked),patch.object(Path,'unlink',windows_unlink):
            connection=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=5)
            try:
                request=(f'POST /api/import?kind=database HTTP/1.0\r\nHost: 127.0.0.1:{self.server.server_port}\r\nX-GPS-RSSI-Workspace: {self.token}\r\nContent-Type: application/octet-stream\r\nContent-Length: 100\r\n\r\npartial').encode()
                connection.sendall(request);connection.shutdown(socket.SHUT_WR)
                response=b''
                while block:=connection.recv(8192):response+=block
                self.assertIn(b'400 Bad Request',response)
            finally:connection.close()
        self.assertTrue(opened)
        self.assertFalse(list((self.root/'data/workspace/imports').iterdir()))

    def test_storage_cleanup_requires_auth_confirmation_and_reviewed_item(self):
        source=self.root/'source.sqlite'
        store=Store(source,'Imported','mobile','replay',{});store.close()
        self.server.workspace.register_import(source,'database','Imported')
        code,catalog=self.request('GET','/api/storage')
        self.assertEqual(code,200)
        row=catalog['items'][0]
        data=dict(id=row['id'],revision=row['revision'],confirmed=True)
        self.assertEqual(self.request('POST','/api/storage/remove',data)[0],403)
        self.assertEqual(self.request('POST','/api/storage/remove',{**data,'confirmed':False},auth=True)[0],400)
        self.assertEqual(self.request('POST','/api/storage/remove',data,auth=True)[0],200)
        self.assertFalse(self.request('GET','/api/storage')[1]['items'])
        self.assertTrue(source.exists())

    def test_artifacts_are_confined_and_bundle_contains_only_result_files(self):
        jobs=self.server.workspace.jobs
        key="a"*32
        folder=jobs.directory/key
        (folder/"result").mkdir(parents=True)
        jobs.entries[key]=dict(id=key, kind="survey", title="Survey", state="completed", created_at="2026-11-06T17:00:00Z")
        (folder/"result/map.html").write_text("<html>results</html>")
        (folder/"process.log").write_text("private diagnostic path")
        self.assertEqual(self.request("GET",f"/files/{key}/map.html",raw=True)[0],200)
        code, partial = self.request("GET", f"/files/{key}/map.html", headers={"Range": "bytes=0-4"}, raw=True)
        self.assertEqual((code, partial), (206, b"<html"))
        self.assertEqual(self.request("GET", f"/files/{key}/map.html", headers={"Range": "bytes=9999-"}, raw=True)[0], 416)
        self.assertEqual(self.request("GET",f"/files/{key}/..%2Fprocess.log",raw=True)[0],400)
        code,payload=self.request("GET",f"/bundle/{key}",raw=True)
        self.assertEqual(code,200)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.namelist(),["map.html"])

    def test_saved_maps_use_scoped_tiles_without_changing_downloads_or_api_access(self):
        key = "b" * 32
        jobs = self.server.workspace.jobs
        folder = jobs.directory / key / "result"
        folder.mkdir(parents=True)
        jobs.entries[key] = dict(id=key, kind="survey", state="completed", title="Survey")
        original = f"<html><script>const tiles='{OSM_TEMPLATE}';</script></html>".encode()
        (folder / "map.html").write_bytes(original)
        code, html = self.request("GET", f"/files/{key}/map.html", raw=True)
        self.assertEqual(code, 200)
        tile_url = re.search(rb"http://127\.0\.0\.1:[0-9]+(/tiles/[^']+)", html)[1].decode()
        route = tile_url.replace("{z}/{x}/{y}", "2/1/1")
        opaque = {"Origin": "null", "Sec-Fetch-Site": "cross-site"}
        tile_capability = route.split("tile_token=", 1)[1]
        with patch("gps_rssi.workspace_server.TileCache.get", return_value=Tile(PNG + b"png", 0)) as get:
            self.assertEqual(self.request("GET", route, headers=opaque, raw=True), (200, PNG + b"png"))
            get.assert_called_once_with(2, 1, 1, referer=None)
            get.reset_mock()
            for invalid in (route.replace(tile_capability, "wrong"), "/tiles/missing"):
                self.assertEqual(self.request("GET", invalid, headers=opaque)[0], 403)
            self.assertEqual(self.request("GET", route, headers={"Origin": "https://attacker.example"})[0], 403)
            get.assert_not_called()
        self.assertEqual(self.request("GET", "/api/state", headers=opaque)[0], 403)
        with patch.object(self.server.workspace, "launch") as launch:
            self.assertEqual(self.request("POST", "/api/jobs", {"kind": "preview"},
                headers={"X-GPS-RSSI-Workspace": tile_capability})[0], 403)
            launch.assert_not_called()
        with patch("gps_rssi.workspace_server.TileCache.get", side_effect=OSError("upstream denied")):
            self.assertEqual(self.request("GET", route, headers=opaque, raw=True)[0], 503)
        self.assertEqual(self.request("GET", f"/files/{key}/map.html?download=1", raw=True), (200, original))
        code, partial = self.request("GET", f"/files/{key}/map.html", headers={"Range": "bytes=0-99"}, raw=True)
        self.assertEqual((code, partial), (206, html[:100]))


if __name__ == "__main__":
    unittest.main()
