"""Loopback-only operator UI, authenticated actions, uploads, and result downloads."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from io import BytesIO
import hashlib
import hmac
import json
import logging
import mimetypes
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import wave
import webbrowser
import zipfile

from .locking import InstanceLock
from .offline import OfflineData
from .elevation import ElevationCache
from .tiles import TileCache, respond_tile
from .workspace import Workspace
from .storage_policy import UPLOAD_LIMITS, required_space, check_space

LOG = logging.getLogger(__name__)
MAX_UPLOAD = UPLOAD_LIMITS["database"]  # Compatibility for recording upload clients.
OSM_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TERRAIN_CONFIG = b'<script id="terrain-config" type="application/json">{"terrainEndpoint":null}</script>'


def make_workspace_server(root, *, port=8780, host="127.0.0.1"):
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("Workspace must bind to loopback")
    workspace = Workspace(root)
    offline = OfflineData(workspace.root / "data")
    tile_cache = TileCache(workspace.root / "data/.gps-rssi-tiles/cache.sqlite")
    token = secrets.token_urlsafe(32)
    tile_token = secrets.token_urlsafe(32)
    terrain_token = secrets.token_urlsafe(32)
    artifact_secret = secrets.token_bytes(32)
    elevations = ElevationCache(workspace.root / "data/.gps-rssi-elevation/cache.sqlite")
    assets = files("gps_rssi").joinpath("assets")

    class Handler(BaseHTTPRequestHandler):
        def valid_site(self):
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            return (self.headers.get("Host") in hosts and
                    (origin is None or origin in {"http://" + host for host in hosts}) and
                    self.headers.get("Sec-Fetch-Site") != "cross-site")

        def respond(self, status, data, content_type="application/json; charset=utf-8", *, filename=None, artifact=False, terrain=False):
            if not isinstance(data, bytes):
                data = json.dumps(data, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "SAMEORIGIN" if artifact else "DENY")
            if terrain and self.headers.get("Origin"):
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Vary", "Origin")
            if artifact:
                self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-downloads; frame-ancestors 'self'")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="' + filename.replace('"', '').replace("\r", "").replace("\n", "") + '"')
            self.end_headers()
            self.wfile.write(data)

        def error(self, status, message):
            try:
                self.respond(status, dict(error=str(message)))
            except (BrokenPipeError, ConnectionResetError):
                pass  # Canceling an upload closes the response connection too.

        def artifact_file(self, path, content_type, *, download=False, job_id=None):
            data = None
            if content_type == "text/html" and not download:
                # Older reports embed direct OSM requests and predate basemap.js.
                # Upgrade their tile URL at serving time, retaining the sandbox.
                template = f"http://127.0.0.1:{self.server.server_port}/tiles/osm/{{z}}/{{x}}/{{y}}.png?tile_token={tile_token}"
                data = path.read_bytes().replace(OSM_TEMPLATE.encode(), template.encode())
                endpoint = f"http://127.0.0.1:{self.server.server_port}/terrain/elevations?terrain_token={terrain_token}"
                config = '<script id="terrain-config" type="application/json">' + json.dumps(dict(terrainEndpoint=endpoint)) + '</script>'
                data = data.replace(TERRAIN_CONFIG, config.encode())
                settings=json.dumps(dict(offline=offline.enabled,map=offline.map_config()),ensure_ascii=True).replace('<',r'\u003c')
                data=data.replace(b'const RssiBasemap=(()=>{',('globalThis.RssiOfflineConfig='+settings+';const RssiBasemap=(()=>{').encode())
                if job_id and path.name == "index.html":
                    # Sandboxed overviews have an opaque origin. Their links get
                    # a read-only capability scoped to this job's result folder.
                    capability = hmac.new(artifact_secret, job_id.encode(), hashlib.sha256).hexdigest()
                    data = re.sub(rb'href="([A-Za-z0-9_.-]+)"',
                        lambda match: b'href="' + match[1] + b'?file_token=' + capability.encode()
                        + b'&amp;tile_token=' + tile_token.encode() + b'"', data)
            size = len(data) if data is not None else path.stat().st_size
            start, end, status = 0, size - 1, 200
            requested = self.headers.get("Range", "")
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if match and size and (match[1] or match[2]):
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]) if match[2] else size - 1, size - 1)
                else:
                    start = max(0, size - int(match[2]))
                if not 0 <= start <= end < size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-downloads; frame-ancestors 'self'")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            with BytesIO(data) if data is not None else path.open("rb") as stream:
                stream.seek(start)
                while length:
                    block = stream.read(min(65536, length))
                    if not block:
                        break
                    self.wfile.write(block)
                    length -= len(block)

        def do_GET(self):
            route = urlsplit(self.path)
            if route.path.startswith("/tiles/"):
                local = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                supplied = parse_qs(route.query).get("tile_token", [""])[0]
                # Sandboxed survey pages have an opaque origin. Their separate
                # capability permits only fixed-provider, read-only map images.
                local_map = (self.headers.get("Host") in local and supplied.isascii()
                             and self.headers.get("Origin") in {None, "null", *("http://" + host for host in local)}
                             and secrets.compare_digest(supplied, tile_token))
                if self.valid_site() or local_map:
                    try:
                        respond_tile(self, tile_cache, route.path)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # Panning and zooming cancel in-flight images.
                else:
                    self.error(403, "Open this map from the local workspace")
                return
            parts = route.path.split("/")
            local_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            artifact_access = False
            if len(parts) == 4 and parts[1] == "files" and re.fullmatch(r"[0-9a-f]{32}", parts[2]):
                supplied = parse_qs(route.query).get("file_token", [""])[0]
                expected = hmac.new(artifact_secret, parts[2].encode(), hashlib.sha256).hexdigest()
                artifact_access = (self.headers.get("Host") in local_hosts and supplied.isascii()
                    and self.headers.get("Origin") in {None, "null", *("http://"+host for host in local_hosts)}
                    and secrets.compare_digest(supplied, expected))
            if not self.valid_site() and not artifact_access:
                self.error(403, "Open the workspace using its local address")
                return
            try:
                if route.path == "/":
                    self.respond(200, assets.joinpath("workspace.html").read_bytes(), "text/html; charset=utf-8")
                elif route.path in ("/assets/workspace.js", "/assets/workspace-status.js", "/assets/results-model.js", "/assets/workspace.css"):
                    name = route.path.rsplit("/", 1)[1]
                    self.respond(200, assets.joinpath(name).read_bytes(), "text/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8")
                elif route.path == "/api/state":
                    self.respond(200, {**workspace.state(), "token": token, "tile_token": tile_token,
                        "closing": getattr(workspace, "closing", False), "closed": getattr(workspace, "closed", False)})
                elif route.path == "/api/storage":
                    self.respond(200, workspace.storage.inventory())
                elif route.path == "/api/results":
                    self.respond(200, workspace.results.list())
                elif route.path.startswith("/files/"):
                    parts = route.path.split("/")
                    if len(parts) != 4:
                        raise ValueError("Result file not found")
                    job_id, name = parts[2], unquote(parts[3])
                    folder = workspace.jobs.folder(job_id) / "result"
                    path = (folder / name).resolve()
                    if name not in workspace.jobs.describe(job_id)["files"] or not path.is_relative_to(folder.resolve()) or not path.is_relative_to(workspace.root):
                        raise ValueError("Result file not found")
                    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    self.artifact_file(path, kind, download=parse_qs(route.query).get("download") == ["1"], job_id=job_id)
                elif route.path.startswith("/bundle/"):
                    job_id = route.path.rsplit("/", 1)[1]
                    job = workspace.jobs.describe(job_id)
                    if job["state"] in ("running", "stopping", "unknown"):
                        raise ValueError("Wait for this operation to finish before downloading the bundle")
                    folder = workspace.jobs.folder(job_id) / "result"
                    with tempfile.TemporaryFile() as stream:
                        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                            for name in job["files"]:
                                path = (folder / name).resolve()
                                if path.is_relative_to(folder.resolve()) and path.is_relative_to(workspace.root):
                                    archive.write(path, arcname=name)
                        size = stream.tell()
                        stream.seek(0)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/zip")
                        self.send_header("Content-Length", str(size))
                        self.send_header("Content-Disposition", f'attachment; filename="{job["kind"]}-{job_id[:8]}.zip"')
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        shutil.copyfileobj(stream, self.wfile)
                else:
                    self.error(404, "Page not found")
            except RuntimeError as exc:
                self.error(409, exc)
            except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
                self.error(400, exc)

        def do_POST(self):
            route = urlsplit(self.path)
            if route.path == "/terrain/elevations":
                local = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                capability = parse_qs(route.query).get("terrain_token", [""])[0]
                allowed = (self.headers.get("Host") in local and capability.isascii()
                           and secrets.compare_digest(capability, terrain_token)
                           and self.headers.get("Origin") in {None, "null", *("http://" + host for host in local)})
                if not allowed:
                    self.error(403, "Reopen the survey from this workspace to load elevations")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8192:
                        raise ValueError("Choose a small elevation batch")
                    self.connection.settimeout(15)
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("Expected elevation locations")
                    self.respond(200, elevations.get_many(data.get("points"), refresh=data.get("refresh", False)), terrain=True)
                except (ValueError, TypeError) as exc:
                    self.respond(400, dict(error=str(exc)), terrain=True)
                except RuntimeError as exc:
                    self.respond(429, dict(error=str(exc)), terrain=True)
                except (BrokenPipeError, ConnectionResetError):
                    return  # Canceling an elevation lookup closes the response.
                except (OSError, sqlite3.Error):
                    self.respond(503, dict(error="USGS elevations unavailable. Cached elevations may still be available; retry later."), terrain=True)
                return
            supplied = self.headers.get("X-GPS-RSSI-Workspace", "")
            if not self.valid_site() or not supplied.isascii() or not secrets.compare_digest(supplied, token):
                self.error(403, "Reload this workspace before using its controls")
                return
            if getattr(workspace, "closing", False) and route.path not in ("/api/stop", "/api/acknowledge", "/api/exit"):
                self.error(409, "The workspace is stopping. Reopen it before starting new work.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                self.connection.settimeout(15)
                if route.path in ("/api/import", "/api/offline/import"):
                    kind = parse_qs(route.query).get("kind", [None])[0]
                    allowed = ('database', 'audio') if route.path == '/api/import' else ('map', 'pack', 'terrain')
                    if kind not in allowed:
                        raise ValueError('Choose a supported recording, audio, or offline file')
                    limit = UPLOAD_LIMITS[kind]
                    if not 0 < length <= limit:
                        self.error(413, f'Choose a {kind} file up to {limit // (1024*1024)} MiB')
                        return
                    if self.headers.get("Content-Type") != "application/octet-stream":
                        raise ValueError("Expected a binary file upload")
                    if not workspace.storage.lock.acquire(blocking=False):
                        raise RuntimeError('Another import or cleanup is in progress')
                    temporary = None
                    try:
                        directory = workspace.inside(workspace.root / "data" / "workspace" / "imports")
                        directory.mkdir(parents=True, exist_ok=True)
                        check_space(directory, required_space(kind, length))
                        # Close before cleanup even when receiving the body fails (Windows).
                        with tempfile.NamedTemporaryFile(dir=directory, prefix=".upload-", delete=False) as stream:
                            temporary = Path(stream.name)
                            remaining = length
                            while remaining:
                                chunk = self.rfile.read(min(65536, remaining))
                                if not chunk:
                                    raise ValueError("Upload was interrupted")
                                stream.write(chunk)
                                remaining -= len(chunk)
                        if route.path == '/api/offline/import':
                            result = {'map':offline.import_mbtiles, 'pack':offline.import_pack, 'terrain':offline.import_terrain}[kind](temporary)
                        else:
                            result = workspace.register_import(temporary, kind, unquote(self.headers.get("X-File-Name", "Imported file")))
                    finally:
                        try:
                            if temporary is not None:
                                temporary.unlink(missing_ok=True)
                        finally:
                            workspace.storage.lock.release()
                    self.respond(201, result)
                    return
                if not 0 < length <= 65536 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Expected a small JSON action")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected an action object")
                if route.path == '/api/storage/remove':
                    self.respond(200, workspace.storage.remove(data.get('id'), data.get('revision'), data.get('confirmed')))
                elif route.path == '/api/offline/mode':
                    offline.set_enabled(data.get('offline'))
                    self.respond(200,offline.status())
                elif route.path == '/api/offline/remove-terrain':
                    from .local_dem import LocalDEM
                    if not workspace.storage.lock.acquire(blocking=False):
                        raise RuntimeError('Another import or cleanup is in progress')
                    try:
                        LocalDEM(offline.data).remove()
                    finally:
                        workspace.storage.lock.release()
                    self.respond(200,offline.status())
                elif route.path == "/api/profiles":
                    self.respond(200, workspace.save_profile(data))
                elif route.path == "/api/sessions":
                    self.respond(201, workspace.sessions.create(data, workspace.profiles()))
                elif route.path == "/api/sessions/finish":
                    self.respond(202, workspace.sessions.finish(data.get("id")))
                elif route.path == "/api/jobs":
                    self.respond(202, workspace.launch(data))
                elif route.path == "/api/prepare-transmit":
                    self.respond(200, workspace.prepare_transmit(data))
                elif route.path == "/api/stop":
                    self.respond(202, workspace.jobs.stop(data.get("id")))
                elif route.path == "/api/acknowledge":
                    workspace.jobs.acknowledge(data.get("id"), data.get("confirmed"))
                    self.respond(200, dict(ok=True))
                elif route.path == "/api/exit":
                    if data.get("confirmed") is not True:
                        raise ValueError("Confirm stopping all managed operations before exiting")
                    if getattr(workspace, "closing", False):
                        self.respond(202, dict(closing=True))
                        return
                    workspace.closing = True
                    def finish():
                        workspace.jobs.close()
                        workspace.closed = True
                        time.sleep(3)
                        self.server.shutdown()
                    threading.Thread(target=finish, daemon=True).start()
                    self.respond(202, dict(closing=True))
                else:
                    self.error(404, "Action not found")
            except RuntimeError as exc:
                self.error(409, exc)
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, wave.Error, zipfile.BadZipFile) as exc:
                self.error(400, exc)

        def log_message(self, fmt, *args):
            LOG.debug(fmt, *args)

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def server_close(self):
            workspace.jobs.close()
            super().server_close()

    server = Server((host, port), Handler)
    workspace.port = server.server_port
    server.workspace = workspace
    return server


def serve_workspace(directory, *, port=8780, no_browser=False):
    directory = Path(directory).expanduser().resolve()
    lock = directory / "data" / "workspace" / "workspace.app.lock"
    if not lock.resolve().is_relative_to(directory):
        raise ValueError("Workspace files must stay inside the app folder")
    with InstanceLock(lock, message="This app folder already has a workspace running. Use its existing browser window."):
        server = make_workspace_server(directory, port=port)
        url = f"http://127.0.0.1:{server.server_port}"
        LOG.info("Workspace: %s", url)
        LOG.info("Closing the browser leaves recording running. Use Exit workspace or Ctrl-C to stop managed operations.")
        try:
            if not no_browser:
                try:
                    if not webbrowser.open(url, new=2):
                        LOG.info("Open %s in your browser", url)
                except Exception:
                    LOG.info("Open %s in your browser", url)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
