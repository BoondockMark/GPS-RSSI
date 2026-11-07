"""Real-browser operator workflow. Run separately with Playwright installed.

No radio is connected: only file-based operations run, and RF review is canceled.
"""

from array import array
import base64
from datetime import datetime, timedelta, timezone
import json
import io
import math
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave
import zipfile

from playwright.sync_api import sync_playwright, expect

from gps_rssi.storage import Store
from gps_rssi.workspace_server import make_workspace_server
from gps_rssi.survey import render_survey, survey_data


class WorkspaceBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = make_workspace_server(self.root, port=0)
        self.profiles = []
        for index, (name, role) in enumerate((("Mobile A", "mobile"), ("Mobile B", "mobile"), ("Monitor", "fixed"))):
            profile = self.server.workspace.save_profile(dict(name=name, role=role, gps=role == "mobile",
                address=f"AA:BB:CC:DD:EE:{index + 1:02x}", label="Synthetic browser-test fixture"))
            self.profiles.append(profile)
            store = Store(self.root / profile["database"], name, role, "benlink_bluetooth", {"label": "Synthetic test"})
            timestamp = datetime.now(timezone.utc).isoformat()
            for value in (0, 50, 100):
                store.sample(dict(status_received_at=timestamp, status=dict(rssi=value, is_in_tx=False, is_in_rx=True,
                    is_sq=True, is_gps_locked=role == "mobile", curr_ch_id=1),
                    position=dict(latitude=33.96, longitude=-117.95, time=timestamp, accuracy=8) if role == "mobile" else None))
            store.close()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temp.cleanup()

    def test_saved_survey_loads_tiles_inside_opaque_sandbox(self):
        key = "c" * 32
        jobs = self.server.workspace.jobs
        folder = jobs.directory / key / "result"
        folder.mkdir(parents=True)
        jobs.entries[key] = dict(id=key, kind="survey", title="Survey", state="completed")
        _, observations, summary = survey_data(self.root / self.profiles[0]["database"])
        (folder / "map.html").write_text(render_survey(observations, summary), encoding="utf-8")
        # Exercise the real HTTP tile route and browser image decoding without
        # depending on external network access or making test traffic to OSM.
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aCfoAAAAASUVORK5CYII=")
        requests = []

        def upstream(request, **kwargs):
            requests.append(request)
            response = io.BytesIO(png)
            response.headers = {}
            return response

        with patch("gps_rssi.tiles.urlopen", side_effect=upstream), sync_playwright() as playwright:
            options = dict(headless=True)
            if os.environ.get("GPS_RSSI_CHROMIUM"):
                options.update(executable_path=os.environ["GPS_RSSI_CHROMIUM"], args=["--no-sandbox"])
            browser = playwright.chromium.launch(**options)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(f"http://127.0.0.1:{self.server.server_port}/files/{key}/map.html", wait_until="networkidle")
                self.assertIn("sandbox allow-scripts allow-downloads;", response.headers["content-security-policy"])
                self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                self.assertEqual(page.evaluate("window.origin"), "null")
                page.wait_for_function("document.querySelectorAll('.leaflet-tile-loaded').length > 0")
                self.assertTrue(page.locator(".leaflet-tile").evaluate_all("images => images.every(i => i.src.includes('/tiles/') && i.complete && i.naturalWidth > 0)"))
                self.assertTrue(requests)
                self.assertTrue(all("GPS-RSSI/" in r.get_header("User-agent") for r in requests))
                count = len(requests)
                page.reload(wait_until="networkidle")
                self.assertEqual(len(requests), count)
                expect(page.locator("#mapped-count")).to_have_text("3")
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_discovered_radio_edits_saved_setup_and_labels_gps_history(self):
        with sync_playwright() as playwright:
            options = dict(headless=True)
            if os.environ.get("GPS_RSSI_CHROMIUM"):
                options.update(executable_path=os.environ["GPS_RSSI_CHROMIUM"], args=["--no-sandbox"])
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport=dict(width=750, height=800))
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            profile = self.profiles[0]
            scan = dict(id="synthetic-scan", kind="scan", state="completed", title="Find nearby radios",
                created_at=datetime.now(timezone.utc).isoformat(), files=[], result=dict(candidates=[
                    dict(name="Discovered mobile", address=profile["address"].replace(":", "-").lower()),
                    dict(name="New radio", address="AA:BB:CC:DD:EE:99")], warnings=[]))
            mode = {"recording": False, "old": False, "locked": True}
            sample = dict(profile["latest"] or self.server.workspace.profile(profile["id"])["latest"])

            def state_route(route):
                response = route.fetch()
                data = response.json()
                data["jobs"].append(scan)
                if mode["recording"]:
                    data["jobs"].append(dict(id="synthetic-recorder", kind="record", state="running",
                        profile=profile["id"], title="Recorder", created_at=scan["created_at"], files=[]))
                current = next(p for p in data["profiles"] if p["id"] == profile["id"])
                current["latest"] = latest = dict(sample)
                latest["received_at"] = (datetime.now(timezone.utc) - timedelta(hours=1) if mode["old"]
                    else datetime.now(timezone.utc)).isoformat()
                latest["gps_locked"] = mode["locked"]
                route.fulfill(response=response, json=data)

            page.route("**/api/state", state_route)
            try:
                page.goto(f"http://127.0.0.1:{self.server.server_port}", wait_until="networkidle")
                mobile = page.locator('.radio-card[data-profile="' + profile["id"] + '"]')
                expect(mobile.locator(".radio-stats")).to_contain_text("Last recorded GPS lock")
                expect(page.locator(".radio-card").filter(has_text="Monitor").locator(".radio-stats")).to_contain_text("Disabled")
                page.get_by_role("button", name="Edit saved radio", exact=True).click()
                expect(page.locator("#radio-dialog-title")).to_have_text("Edit radio")
                expect(page.locator("#radio-name")).to_have_value(profile["name"])
                page.locator("#radio-name").fill("Renamed mobile")
                page.locator("#radio-label").fill("Updated antenna")
                page.locator("#save-radio").click()
                expect(page.locator("#radio-dialog")).not_to_be_visible()
                saved = self.server.workspace.profile(profile["id"])
                self.assertEqual(saved["name"], "Renamed mobile")
                self.assertEqual(saved["label"], "Updated antenna")
                self.assertEqual(saved["database"], profile["database"])
                self.assertEqual(saved["port"], profile["port"])
                self.assertEqual(len(self.server.workspace.profiles()), 3)

                page.get_by_role("button", name="Use this radio", exact=True).click()
                expect(page.locator("#radio-dialog-title")).to_have_text("Add a radio")
                page.locator("#radio-name").fill("New discovery")
                page.locator("#save-radio").click()
                expect(page.locator("#radio-dialog")).not_to_be_visible()
                expect(page.locator("#configured-count")).to_have_text("4")
                expect(page.locator(".radio-card").filter(has_text="New discovery").locator(".radio-stats")).to_contain_text("Unknown")

                mode["recording"] = True
                page.evaluate("refresh()")
                expect(mobile.locator(".radio-stats small").last).to_have_text("GPS lock")
                page.get_by_role("button", name="Edit saved radio", exact=True).click()
                expect(page.locator("#radio-dialog")).not_to_be_visible()
                expect(page.locator("#notice-text")).to_contain_text("Stop this radio")
                mode["old"] = True
                page.evaluate("refresh()")
                expect(mobile.locator(".radio-stats small").last).to_have_text("Last recorded GPS lock")
                mode["locked"] = False
                page.evaluate("refresh()")
                expect(mobile.locator(".radio-stats")).to_contain_text("No lock")
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_operator_workflow_without_rf(self):
        with sync_playwright() as playwright:
            options = dict(headless=True)
            if os.environ.get("GPS_RSSI_CHROMIUM"):
                options.update(executable_path=os.environ["GPS_RSSI_CHROMIUM"], args=["--no-sandbox"])
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport=dict(width=1440, height=1000), timezone_id="America/Los_Angeles", accept_downloads=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://**/*", lambda route: route.abort())
            try:
                page.goto(f"http://127.0.0.1:{self.server.server_port}", wait_until="networkidle")
                expect(page.locator(".radio-card")).to_have_count(3)
                page.locator("#add-radio").click()
                page.locator("#radio-name").fill("Spare kit")
                page.locator("#radio-address").fill("AA:BB:CC:DD:EE:04")
                page.locator("#save-radio").click()
                expect(page.locator("#radio-dialog")).not_to_be_visible()
                expect(page.locator("#configured-count")).to_have_text("4")
                page.locator(".radio-card").filter(has_text="Spare kit").get_by_role("button", name="Edit", exact=True).click()
                page.locator("#radio-name").fill("Unsaved edit")
                page.wait_for_timeout(2200)
                expect(page.locator("#radio-name")).to_have_value("Unsaved edit")
                expect(page.locator("#radio-name")).to_be_focused()
                page.locator("#radio-dialog").get_by_role("button", name="Cancel", exact=True).click()

                page.locator('[data-view="surveys"]').click()
                fixed = page.locator('#database-list input[value="profile:' + self.profiles[2]["id"] + '"]')
                fixed.uncheck()
                page.wait_for_timeout(2200)
                expect(fixed).not_to_be_checked()
                page.locator("#build-survey").click()
                survey = page.locator(".job").filter(has_text="Combined survey").first
                expect(survey.locator(".badge")).to_have_text("Ready", timeout=30000)
                with page.expect_download() as info:
                    survey.get_by_role("link", name="Download ZIP", exact=True).click()
                target = self.root / "download.zip"
                info.value.save_as(target)
                with zipfile.ZipFile(target) as archive:
                    self.assertIn("map.html", archive.namelist())
                    self.assertEqual(json.loads(archive.read("summary.json"))["mapped_points"], 6)

                page.locator("#export-database").select_option("profile:" + self.profiles[0]["id"])
                page.locator("#export-format").select_option("geojson")
                page.locator("#export-file").click()
                export = page.locator(".job").filter(has_text="GEOJSON export").first
                expect(export.locator(".badge")).to_have_text("Ready", timeout=30000)

                page.locator('[data-view="audio"]').click()
                page.locator("#preview-tone").click()
                preview = page.locator(".job").filter(has_text="Test tone preview").first
                expect(preview.locator(".badge")).to_have_text("Ready", timeout=30000)
                expect(preview.locator("audio")).to_have_count(1)
                page.locator("#transmitter-radio").select_option(self.profiles[0]["id"])
                page.locator("#review-transmit").click()
                expect(page.locator("#transmit-dialog")).to_be_visible()
                expect(page.locator("#confirm-transmit")).to_be_disabled()
                page.locator("#transmit-confirm").check()
                expect(page.locator("#confirm-transmit")).to_be_enabled()
                page.locator("#transmit-dialog").get_by_role("button", name="Cancel", exact=True).click()
                self.assertFalse(any(job["kind"] == "transmit" for job in self.server.workspace.jobs.entries.values()))

                audio = self.root / "synthetic.wav"
                samples = array("h", [round(6000 * math.sin(2 * math.pi * 1000 * i / 32000) +
                    600 * math.sin(2 * math.pi * 2000 * i / 32000)) for i in range(32000)] + [0] * 32000)
                with wave.open(str(audio), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(32000)
                    wav.writeframes(samples.tobytes())
                page.locator("#audio-file").set_input_files(str(audio))
                expect(page.locator("#audio-import-status")).to_contain_text("Imported", timeout=10000)
                page.locator("#analyze-audio").click()
                analysis = page.locator(".job").filter(has_text="Signal quality analysis").first
                expect(analysis.locator(".badge")).to_have_text("Ready", timeout=30000)
                expect(analysis).to_contain_text("1 of 2 valid windows")
                with page.expect_popup() as info:
                    analysis.get_by_role("link", name="View report", exact=False).click()
                report = info.value
                report.wait_for_load_state()
                self.assertEqual(report.locator("circle").count(), 1)
                report.close()

                for width in (320, 390, 768, 1440):
                    page.set_viewport_size(dict(width=width, height=900))
                    for view in ("radios", "live", "surveys", "audio"):
                        page.locator(f'[data-view="{view}"]').click()
                        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, view))
                self.assertEqual(errors, [])
                page.locator("#exit-workspace").click()
                page.locator("#confirm-exit").click()
                expect(page.locator("#notice-text")).to_contain_text("Workspace closed", timeout=15000)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
