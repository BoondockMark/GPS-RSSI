import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from gps_rssi.storage import Store
from gps_rssi.workspace_server import make_workspace_server


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


if __name__ == "__main__":
    unittest.main()
