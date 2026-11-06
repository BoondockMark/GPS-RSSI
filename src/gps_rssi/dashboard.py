"""Loopback dashboard with optional combined-app lifecycle controls."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import logging
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit

from .storage import load_log
from .measurements import select_points, utc_now

LOG = logging.getLogger(__name__)


def area_overlay(name):
    if name == "none":
        return None
    if name != "la-habra-heights":
        raise ValueError("Unknown planning area")
    return json.loads(files("gps_rssi").joinpath("assets/la-habra-heights.geojson").read_text(encoding="utf-8"))


def snapshot(path, *, after=None, generation=None, station=None, session=None,
             source="live", max_age=10, max_accuracy=100, max_points=5000):
    filters = {"station": station, "session": session, "source": source}
    initial = after is None
    limit = max_points if initial else 1000
    sessions, samples, events = load_log(path, **filters, after_id=after,
                                       sample_limit=limit, event_limit=20, newest=initial)
    current_generation = sessions[0]["id"] if sessions else "empty"
    reset = initial or generation != current_generation
    if reset and not initial:
        return snapshot(path, **filters, max_age=max_age, max_accuracy=max_accuracy, max_points=max_points)
    points, excluded = select_points(sessions, samples, max_age=max_age, max_accuracy=max_accuracy)
    latest = None
    if samples:
        s = samples[-1]
        latest = {k: s[k] for k in ("received_at", "direction", "rssi_value", "rssi_unit", "is_in_rx", "is_in_tx", "gps_locked")}
    link_events = [e for e in events if e["kind"] in
                   ("connected", "connection_error", "disconnected", "disconnect_error", "stopped")]
    event = link_events[-1] if link_events else None
    return {"points": points, "excluded": excluded, "processed": len(samples),
            "cursor": samples[-1]["id"] if samples else (after or 0),
            "generation": current_generation, "reset": reset,
            "more": not initial and len(samples) == limit,
            "latest": latest, "link_event": event, "server_time": utc_now()}


def make_server(path, *, host="127.0.0.1", port=8765, refresh=2,
                area="la-habra-heights", **filters):
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("Dashboard must bind to 127.0.0.1 or localhost")
    snapshot(path, **filters)  # Validate the database before announcing readiness.
    config = {"refresh": refresh, "max_points": filters.get("max_points", 5000),
              "max_age": filters.get("max_age", 10), "max_accuracy": filters.get("max_accuracy", 100),
              "source": filters.get("source", "live"), "area": area_overlay(area)}
    assets = files("gps_rssi").joinpath("assets")
    allowed = {
        "/assets/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
        "/assets/leaflet.js": ("leaflet/leaflet.js", "text/javascript; charset=utf-8"),
        "/assets/leaflet.css": ("leaflet/leaflet.css", "text/css; charset=utf-8"),
    }
    for image in ("layers.png", "layers-2x.png", "marker-icon.png", "marker-icon-2x.png", "marker-shadow.png"):
        allowed[f"/assets/images/{image}"] = (f"leaflet/{image}", "image/png")

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def json_response(self, status, data):
            self.respond(status, json.dumps(data, allow_nan=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            route = urlsplit(self.path)
            if route.path == "/":
                self.respond(200, assets.joinpath("dashboard.html").read_bytes(), "text/html; charset=utf-8")
            elif route.path == "/api/config":
                controls = self.server.controls
                self.json_response(200, {**config, "controls": {
                    "enabled": controls is not None,
                    **({"token": controls.token, "operation": controls.status(),
                        "database": str(path.resolve())} if controls else {})}})
            elif route.path == "/api/control":
                controls = self.server.controls
                if controls is None:
                    self.json_response(409, {"error": "Use gps-rssi start for reset/exit controls"})
                    return
                operation = controls.status()
                requested = parse_qs(route.query).get("id", [None])[0]
                if requested and requested != operation.get("id"):
                    self.json_response(404, {"error": "Operation not found"})
                    return
                self.json_response(200, operation)
                # A running response must not acknowledge a completion that
                # happened concurrently while this response was being sent.
                if operation.get("action") == "exit" and operation["state"] == "completed":
                    controls.acknowledge(operation.get("id"))
            elif route.path == "/api/snapshot":
                query = parse_qs(route.query)
                try:
                    value = query.get("after", [None])[0]
                    after = None if value is None else int(value)
                    if after is not None and not 0 <= after <= 9223372036854775807:
                        raise ValueError("invalid cursor")
                except ValueError:
                    self.json_response(400, {"error": "after must be a nonnegative SQLite sample ID"})
                    return
                try:
                    result = snapshot(path, after=after, generation=query.get("generation", [None])[0], **filters)
                    self.json_response(200, result)
                except (sqlite3.Error, OSError, ValueError) as exc:
                    LOG.warning("Dashboard read failed: %s", exc)
                    self.json_response(503, {"error": "Could not read log; retrying. Check server console."})
            elif route.path in allowed:
                filename, content_type = allowed[route.path]
                self.respond(200, assets.joinpath(filename).read_bytes(), content_type)
            else:
                self.json_response(404, {"error": "Not found"})

        def log_message(self, fmt, *args):
            LOG.debug(fmt, *args)

        def do_POST(self):
            if urlsplit(self.path).path != "/api/control":
                self.json_response(404, {"error": "Not found"})
                return
            controls = self.server.controls
            if controls is None:
                self.json_response(409, {"error": "Use gps-rssi start for reset/exit controls"})
                return
            supplied_token = self.headers.get("X-GPS-RSSI-Control", "")
            if not supplied_token.isascii() or not secrets.compare_digest(supplied_token, controls.token):
                self.json_response(403, {"error": "Reload this dashboard before using controls"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("Invalid request size")
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise ValueError("Expected JSON")
                self.connection.settimeout(5)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or data.get("action") not in ("reset", "exit"):
                    raise ValueError("Expected reset or exit")
                if data["action"] == "reset" and data.get("confirm") is not True:
                    raise ValueError("Database reset requires confirmation")
            except (ValueError, UnicodeError, TimeoutError) as exc:
                self.json_response(400, {"error": str(exc)})
                return
            try:
                operation = controls.submit(data["action"])
            except RuntimeError as exc:
                self.json_response(409, {"error": str(exc)})
                return
            self.json_response(202, operation)

    server = ThreadingHTTPServer((host, port), Handler)
    server.controls = None  # Enabled only by the combined logger/dashboard supervisor.
    server.daemon_threads = True
    return server


def serve(path, **options):
    server = make_server(path, **options)
    LOG.info("Dashboard: http://127.0.0.1:%d (Ctrl-C to stop; logger runs separately)", server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
