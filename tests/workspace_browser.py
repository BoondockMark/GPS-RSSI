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
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import wave
import zipfile

from playwright.sync_api import sync_playwright, expect

from gps_rssi.storage import Store
from gps_rssi.workspace_server import make_workspace_server
from gps_rssi.survey import render_survey, survey_data


class WorkspaceBrowserTests(unittest.TestCase):
    def test_imported_terrain_supplies_offline_survey_elevations(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import transform
        x, y = transform('EPSG:4326', 'EPSG:26911', [-117.95], [33.96])
        path = self.root / 'ground.tif'
        with rasterio.open(path, 'w', driver='GTiff', count=1, dtype='float32', width=16, height=16,
                           crs='EPSG:26911', transform=from_origin(x[0]-8, y[0]+8, 1, 1), nodata=-9999) as dst:
            dst.write(np.full((16, 16), 123, dtype='float32'), 1)
            dst.update_tags(vertical_datum='NAVD88', units='meters', source='Browser terrain fixture')
        with sync_playwright() as playwright:
            options = dict(headless=True)
            if os.environ.get('GPS_RSSI_CHROMIUM'):
                options.update(executable_path=os.environ['GPS_RSSI_CHROMIUM'], args=['--no-sandbox'])
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport=dict(width=1400, height=1000))
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            try:
                page.goto(f'http://127.0.0.1:{self.server.server_port}', wait_until='networkidle')
                page.locator('[data-view="offline"]').click()
                page.locator('#offline-terrain-file').set_input_files(str(path))
                expect(page.locator('#offline-import-status')).to_contain_text('Imported ground.tif')
                expect(page.locator('#offline-terrain-description')).to_contain_text('Browser terrain fixture')
                page.locator('#offline-mode').check()
                with patch('gps_rssi.elevation.urlopen', side_effect=AssertionError('external elevations forbidden')):
                    page.locator('[data-view="surveys"]').click()
                    page.locator('#database-list input').first.check()
                    page.locator('#build-survey').click()
                    job = page.locator('.job').filter(has_text='Combined survey').first
                    expect(job.locator('.badge')).to_have_text('Ready', timeout=30000)
                    with page.expect_popup() as info:
                        job.get_by_role('link', name='Open survey ↗', exact=True).click()
                    report = info.value
                    report.wait_for_load_state('networkidle')
                    report.locator('#show-routes').click()
                    report.locator('#load-route-elevation').click()
                    expect(report.locator('#terrain-route-note')).to_contain_text('3 of 3')
                    expect(report.locator('#terrain-route-note')).to_contain_text('Local terrain GeoTIFF')
                page.locator('[data-view="offline"]').click()
                page.locator('#remove-offline-terrain').click()
                expect(page.locator('#offline-terrain-description')).to_contain_text('No local terrain imported')
                self.assertFalse(errors, errors)
            finally:
                browser.close()

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

    def test_portable_offline_data_controls_and_regional_map_without_upstream(self):
        # A one-tile world raster tests local serving at all overzoomed scales.
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a52kAAAAASUVORK5CYII=')
        mb=self.root/'region.mbtiles'
        with sqlite3.connect(mb) as db:
            db.executescript('CREATE TABLE metadata(name TEXT,value TEXT); CREATE TABLE tiles(zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB);')
            db.executemany('INSERT INTO metadata VALUES (?,?)',[('name','Browser topo fixture'),('attribution','Fixture source <script>bad()</script>')])
            db.execute('INSERT INTO tiles VALUES (0,0,0,?)',(png,))
        with sync_playwright() as playwright:
            options=dict(headless=True)
            if os.environ.get('GPS_RSSI_CHROMIUM'):
                options.update(executable_path=os.environ['GPS_RSSI_CHROMIUM'],args=['--no-sandbox'])
            browser=playwright.chromium.launch(**options)
            page=browser.new_page(viewport=dict(width=1400,height=1000));errors=[];requests=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.context.on('request',lambda request:requests.append(request.url))
            try:
                page.goto(f'http://127.0.0.1:{self.server.server_port}',wait_until='networkidle')
                page.locator('[data-view="offline"]').click()
                page.locator('#offline-map-file').set_input_files(str(mb))
                expect(page.locator('#offline-import-status')).to_contain_text('Imported region.mbtiles')
                expect(page.locator('#offline-map-description')).to_contain_text('Browser topo fixture')
                page.locator('#offline-mode').check()
                expect(page.locator('#offline-summary')).to_contain_text('OFFLINE')
                page.locator('#export-offline-pack').click()
                job=page.locator('.job').filter(has_text='Portable offline data').first
                expect(job.locator('.badge')).to_have_text('Ready',timeout=30000)
                job_id=job.get_attribute('data-job')
                pack=self.server.workspace.jobs.folder(job_id)/'result/offline-data.zip'
                self.assertTrue(pack.is_file())
                page.locator('#offline-pack-file').set_input_files(str(pack))
                expect(page.locator('#offline-import-status')).to_contain_text('Imported offline-data.zip')
                with patch('gps_rssi.tiles.urlopen',side_effect=AssertionError('external tiles forbidden')), patch('gps_rssi.elevation.urlopen',side_effect=AssertionError('external elevations forbidden')):
                    page.locator('[data-view="surveys"]').click()
                    page.locator('#database-list input').first.check()
                    page.locator('#build-survey').click()
                    survey=page.locator('.job').filter(has_text='Combined survey').first
                    expect(survey.locator('.badge')).to_have_text('Ready',timeout=30000)
                    with page.expect_popup() as info:
                        survey.get_by_role('link',name='Open survey ↗',exact=True).click()
                    report=info.value;report.on('pageerror',lambda error:errors.append(str(error)))
                    report.wait_for_load_state('networkidle')
                    expect(report.locator('#basemap-status')).to_contain_text('Offline map tiles use saved data only')
                    expect(report.locator('#basemap-status')).to_contain_text('Browser topo fixture')
                    self.assertTrue(report.locator('.leaflet-tile-loaded').count())
                    self.assertEqual(report.locator('.leaflet-control-attribution script').count(),0)
                    # A gap queues elevation points without issuing even one USGS call.
                    report.locator('#show-routes').click()
                    report.locator('#load-route-elevation').click()
                    expect(report.locator('#terrain-route-note')).to_contain_text('0 of')
                    self.assertTrue(self.server.workspace.state()['offline_data']['pending'].get('elevation'))
                    self.assertFalse(any(url.startswith('https:') for url in requests),requests)
                self.assertFalse(errors,errors)
            finally:
                browser.close()

    def test_saved_survey_loads_tiles_inside_opaque_sandbox(self):
        self.check_survey_backgrounds(legacy=True)

    def test_offline_training_survey_from_workspace_and_standalone_file(self):
        with sync_playwright() as playwright:
            options = dict(headless=True)
            if os.environ.get('GPS_RSSI_CHROMIUM'):
                options.update(executable_path=os.environ['GPS_RSSI_CHROMIUM'], args=['--no-sandbox'])
            browser = playwright.chromium.launch(**options)
            context = browser.new_context(viewport=dict(width=1400,height=1000))
            requests, errors = [], []
            context.on('request', lambda request: requests.append(request.url))
            context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
            page = context.new_page()
            try:
                page.goto(f'http://127.0.0.1:{self.server.server_port}', wait_until='networkidle')
                page.locator('[data-view="surveys"]').click()
                page.locator('#create-demo').click()
                job = page.locator('.job').filter(has_text='Synthetic test survey').first
                expect(job.locator('.badge')).to_have_text('Ready',timeout=30000)
                with page.expect_popup() as info:
                    job.get_by_role('link',name='Open survey ↗',exact=True).click()
                report = info.value
                report.wait_for_load_state('networkidle')
                expect(report.locator('#training-panel')).to_be_visible()
                expect(report.locator('#mapped-count')).to_have_text('63')
                cases=[('Sampled clear','60% first Fresnel envelope clears sampled terrain'),
                       ('Fresnel intrusion','intrudes into the 60% first Fresnel envelope'),
                       ('Blocked line','Sampled terrain crosses'),
                       ('Missing elevation','Unknown path clearance'),
                       ('Missing frequency','Fresnel clearance unknown')]
                for label,expected in cases:
                    report.locator('#training-cases').get_by_role('button',name=label,exact=True).click()
                    expect(report.locator('#terrain-status')).to_contain_text(expected)
                    expect(report.locator('#terrain-status')).to_contain_text('SYNTHETIC TRAINING')
                report.get_by_role('button',name='Load synthetic route elevations',exact=True).click()
                expect(report.locator('#terrain-route-note')).to_contain_text('Synthetic training terrain')
                report.locator('#show-comparison').click()
                expect(report.locator('#matched-rows tr')).to_have_count(29)
                report.locator('#match-frequency').uncheck()
                expect(report.locator('#matched-rows tr')).to_have_count(31)
                report.locator('#match-radius').fill('5')
                report.locator('#match-radius').press('Tab')
                expect(report.locator('#matched-rows tr')).to_have_count(0)
                self.assertFalse(any('/terrain/elevations' in url or '/tiles/' in url or url.startswith('https:') for url in requests),requests)
                self.assertFalse((self.root/'data/.gps-rssi-elevation').exists())
                demo = next(j for j in self.server.workspace.jobs.entries.values() if j['kind']=='demo')
                file = self.server.workspace.jobs.folder(demo['id'])/'result/map.html'
                self.assertEqual({j['kind'] for j in self.server.workspace.jobs.entries.values()},{'demo'})
                # Verify the delivered HTML, not a mocked network responder, offline.
                context.set_offline(True)
                standalone = context.new_page()
                # Managed Chrome blocks file:// navigation by administrator policy.
                # Load the exact standalone HTML offline, without server injection.
                standalone.set_content(file.read_text(encoding='utf-8'),wait_until='load')
                expect(standalone.locator('#training-panel')).to_be_visible()
                for label,expected in cases:
                    standalone.locator('#training-cases').get_by_role('button',name=label,exact=True).click()
                    expect(standalone.locator('#terrain-status')).to_contain_text(expected)
                standalone.locator('#show-comparison').click()
                expect(standalone.locator('#matched-rows tr')).to_have_count(29)
                for width in (390,768,1400):
                    standalone.set_viewport_size(dict(width=width,height=900))
                    self.assertTrue(standalone.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                self.assertEqual(errors,[])
            finally:
                browser.close()

    def test_routes_comparison_and_terrain_follow_real_measurements(self):
        key = 'd' * 32
        jobs = self.server.workspace.jobs
        folder = jobs.directory / key / 'result'
        folder.mkdir(parents=True)
        jobs.entries[key] = dict(id=key, kind='survey', title='Survey', state='completed')
        observations = []
        for session in range(2):
            for index, rssi in enumerate((80, 30, 60, 10)):
                observations.append(dict(id=session*4+index, original_sample=index+1, station=f'Radio {session}',
                    session=f'session-{session}', label=f'Antenna {session}', source='live', time=f'2026-11-07T12:00:{index*5:02d}Z',
                    direction='downlink', rssi=rssi, rssi_unit='benshi_relative_0_100', lat=34+index*.001,
                    lon=-118+session*.0001, channel=1, frequency_hz=None if session==1 and index==3 else 146520000, squelch_open=True, location_reason=''))
        summary = dict(source='live', station_count=2, session_count=2, selected_samples=8,
            sessions=[dict(id=f'session-{i}',station=f'Radio {i}',started_at='2026-11-07T12:00:00Z') for i in range(2)])
        (folder / 'map.html').write_text(render_survey(observations, summary), encoding='utf-8')
        queries = []
        mode = {'missing': False, 'block': False, 'flat':False, 'resolution':10, 'date':'2020-01-02'}
        entered, release = threading.Event(), threading.Event()

        def elevation(request, **kwargs):
            params = parse_qs(urlsplit(request.full_url).query)
            queries.append(params)
            if mode['block']:
                entered.set()
                release.wait(5)
            lat = float(params['y'][0])
            value = -1000000 if mode['missing'] else 800 if not mode['flat'] and 34.0008 < lat < 34.0022 else 300
            return io.BytesIO(json.dumps(dict(value=value,resolution=mode['resolution'],resolutionUnits='Meters',
                rasterId=100 if mode['resolution']==10 else 101,attributes=dict(AcquisitionDate=mode['date'],Name='Fixture ground DEM'))).encode())

        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aCfoAAAAASUVORK5CYII=')
        def tile(*args, **kwargs):
            response = io.BytesIO(png); response.headers = {}; return response

        with patch('gps_rssi.elevation.urlopen', side_effect=elevation), patch('gps_rssi.tiles.urlopen', side_effect=tile), sync_playwright() as playwright:
            options = dict(headless=True)
            if os.environ.get('GPS_RSSI_CHROMIUM'):
                options.update(executable_path=os.environ['GPS_RSSI_CHROMIUM'], args=['--no-sandbox'])
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport=dict(width=1400,height=1000))
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            try:
                origin = f'http://127.0.0.1:{self.server.server_port}'
                token = page.request.get(origin+'/api/state').json()['tile_token']
                page.goto(f'{origin}/files/{key}/map.html?tile_token={token}', wait_until='networkidle')
                self.assertEqual(queries, [])  # No elevation sharing until requested.
                expect(page.locator('#route-charts circle[role=button]')).to_have_count(8)
                page.locator('#route-charts circle[role=button]').first.click()
                expect(page.locator('#measurement-selection')).to_contain_text('original sample 1')
                page.locator('#show-comparison').click()
                expect(page.locator('#match-summary')).to_contain_text('3 one-to-one pairs')
                expect(page.locator('#matched-rows tr')).to_have_count(3)
                page.get_by_role('button',name='Inspect pair 1',exact=True).click()
                for pane in ('a','b'):
                    expect(page.locator(f'#compare-{pane}-chart circle[stroke="#e11d48"]')).to_have_count(1)
                page.locator('#match-radius').fill('5')
                page.locator('#match-radius').press('Tab')
                expect(page.locator('#matched-rows tr')).to_have_count(0)
                page.locator('#match-radius').fill('25')
                page.locator('#match-radius').press('Tab')
                page.locator('#match-frequency').uncheck()
                expect(page.locator('#matched-rows tr')).to_have_count(4)
                expect(page.locator('#match-summary')).to_contain_text('Frequency constraint is off')
                page.get_by_role('button',name='Inspect pair 4',exact=True).click()
                expect(page.locator('#measurement-selection')).to_contain_text('original sample 4')
                page.locator('#match-frequency').check()
                expect(page.locator('#compare-b-chart circle[stroke="#e11d48"]')).to_have_count(0)
                expect(page.locator('#compare-a-chart circle')).to_have_count(4)
                expect(page.locator('#compare-b-chart circle')).to_have_count(4)
                page.locator('#compare-a-map .leaflet-control-zoom-in').click()
                page.wait_for_function("""() => {
                    const tiles = id => [...document.querySelectorAll(id+' .leaflet-tile')].map(i=>i.src).sort().join('|');
                    return tiles('#compare-a-map') === tiles('#compare-b-map');
                }""")
                first = page.locator('#compare-a-map .leaflet-tile').evaluate_all('imgs=>imgs.map(i=>i.src).sort()')
                second = page.locator('#compare-b-map .leaflet-tile').evaluate_all('imgs=>imgs.map(i=>i.src).sort()')
                self.assertEqual(first, second)
                page.locator('#load-route-elevation').click()
                expect(page.locator('#terrain-route-note')).to_contain_text('4 of 4', timeout=20000)
                self.assertEqual(len(queries),4)
                page.locator('#route-charts circle[role=button]').nth(3).click()
                for field,value in (('fixed-lat','34'),('fixed-lon','-118.0002'),('fixed-height','10'),('mobile-height','2')):
                    page.locator('#'+field).fill(value)
                page.locator('#load-path-elevation').click()
                expect(page.locator('#terrain-status')).to_contain_text('Sampled terrain crosses', timeout=20000)
                expect(page.locator('#path-profile svg')).to_have_count(1)
                expect(page.locator('#terrain-status')).to_contain_text('ground sample spacing')
                expect(page.locator('#terrain-status')).to_contain_text('60% first Fresnel envelope')
                expect(page.locator('#path-profile polygon')).to_have_count(1)
                page.get_by_text('Path elevation source details',exact=True).click()
                expect(page.locator('#path-source-rows')).to_contain_text('2020-01-02')
                expect(page.locator('#path-source-rows')).to_contain_text('10.00 × 10.00')
                expect(page.locator('#path-source-note')).to_contain_text('not vertical accuracy')
                if os.environ.get('GPS_RSSI_TEST_SCREENSHOT'):
                    page.screenshot(path=os.environ['GPS_RSSI_TEST_SCREENSHOT'], full_page=True)
                    page.locator('#path-profile').screenshot(path=os.environ['GPS_RSSI_TEST_SCREENSHOT'].replace('.png','-profile.png'))
                    page.locator('#location-comparison').screenshot(path=os.environ['GPS_RSSI_TEST_SCREENSHOT'].replace('.png','-pairs.png'))
                page.locator('#mobile-height').fill('5')
                expect(page.locator('#path-profile svg')).to_have_count(0)
                expect(page.locator('#terrain-status')).to_contain_text('cleared')
                # A 1 m source needs >600 samples here. The plot must retain the
                # full ground profile, and a smaller budget must be disclosed.
                mode.update(resolution=1,flat=True)
                for field,value in (('fixed-lat','33.995'),('fixed-height','60'),('mobile-height','60')):
                    page.locator('#'+field).fill(value)
                page.locator('#load-path-elevation').click()
                expect(page.locator('#path-sampling-note')).to_contain_text('1025 ground samples',timeout=60000)
                expect(page.locator('#path-sampling-note')).to_contain_text('Spacing targets met')
                self.assertGreater(len(page.locator('#path-profile polyline').get_attribute('points').split()),600)
                page.locator('#path-budget').select_option('257')
                page.locator('#load-path-elevation').click()
                expect(page.locator('#path-sampling-note')).to_contain_text('SAMPLING LIMITED',timeout=30000)
                expect(page.locator('#terrain-status')).to_contain_text('SAMPLING LIMITED')
                mode['date']='2022-03-04'
                page.locator('#refresh-elevation').check()
                page.locator('#load-path-elevation').click()
                expect(page.locator('#path-source-rows')).to_contain_text('2022-03-04',timeout=30000)
                page.locator('#refresh-elevation').uncheck()
                page.locator('#path-budget').select_option('1025')
                mode.update(resolution=10,flat=False)
                for field,value in (('fixed-lat','34'),('fixed-height','10'),('mobile-height','5')):
                    page.locator('#'+field).fill(value)
                page.locator('#route-charts circle[role=button]').nth(7).click()
                expect(page.locator('#path-frequency-note')).to_contain_text('No valid frequency')
                page.locator('#load-path-elevation').click()
                expect(page.locator('#terrain-status')).to_contain_text('Fresnel clearance unknown', timeout=20000)
                expect(page.locator('#path-profile polygon')).to_have_count(0)
                page.locator('#path-frequency-source').select_option('manual')
                page.locator('#path-frequency').fill('146.52')
                page.locator('#load-path-elevation').click()
                expect(page.locator('#terrain-status')).to_contain_text('entered frequency 146.520000 MHz', timeout=20000)
                expect(page.locator('#path-profile polygon')).to_have_count(1)
                page.locator('#path-frequency').fill('430')
                expect(page.locator('#path-profile svg')).to_have_count(0)
                mode['missing'] = True
                page.locator('#fixed-lon').fill('-118.001')
                page.locator('#load-path-elevation').click()
                expect(page.locator('#terrain-status')).to_contain_text('Unknown path clearance', timeout=20000)
                mode['block'] = True
                page.locator('#terrain-session').select_option('session-1')
                page.locator('#load-route-elevation').click()
                self.assertTrue(entered.wait(5))
                page.locator('#cancel-elevation').click()
                mode['block'] = False
                release.set()
                expect(page.locator('#terrain-status')).to_contain_text('canceled')
                expect(page.locator('#terrain-route-charts svg')).to_have_count(0)
                page.locator('#station-filter').select_option('Radio 0')
                expect(page.locator('#compare-b-note')).to_contain_text('No located measurements')
                expect(page.locator('#matched-rows tr')).to_have_count(0)
                for width in (390,768,1400):
                    page.set_viewport_size(dict(width=width,height=900))
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width)
                self.assertEqual(errors,[])
            finally:
                release.set()
                browser.close()

    def test_survey_switches_all_backgrounds_inside_opaque_sandbox(self):
        self.check_survey_backgrounds(legacy=False)

    def check_survey_backgrounds(self, *, legacy):
        key = "c" * 32
        jobs = self.server.workspace.jobs
        folder = jobs.directory / key / "result"
        folder.mkdir(parents=True)
        jobs.entries[key] = dict(id=key, kind="survey", title="Survey", state="completed")
        _, observations, summary = survey_data(self.root / self.profiles[0]["database"])
        # Reproduce a saved report generated before the cached basemap helper.
        html = render_survey(observations, summary)
        if legacy:
            html = html.replace("RssiBasemap.addTo(map,{background:explorer?.isDemo?'none':'osm'});",
                "L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);")
        (folder / "map.html").write_text(html, encoding="utf-8")
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
                origin = f"http://127.0.0.1:{self.server.server_port}"
                token = page.request.get(origin + '/api/state').json()['tile_token']
                response = page.goto(f"{origin}/files/{key}/map.html?tile_token={token}", wait_until="networkidle")
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
                if not legacy:
                    backgrounds = page.get_by_label('Map background', exact=True)
                    for label, provider in (('Topographic','topo'), ('Shaded relief','relief'), ('Satellite','satellite'), ('Street','osm')):
                        backgrounds.get_by_text(label, exact=True).click()
                        page.wait_for_function("provider => [...document.querySelectorAll('.leaflet-tile-loaded')].some(i => i.src.includes('/tiles/' + provider + '/'))", arg=provider)
                        expect(page.locator('#basemap-status')).to_contain_text(label)
                        expect(page.locator('#mapped-count')).to_have_text('3')
                    backgrounds.get_by_text('No background', exact=True).click()
                    expect(page.locator('.leaflet-tile')).to_have_count(0)
                    expect(page.locator('#mapped-count')).to_have_text('3')
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

    def test_passive_sweep_without_audio_tools_never_reviews_or_requests_rf(self):
        from gps_rssi.storage import load_log
        workspace=self.server.workspace
        original_launch=workspace.jobs.launch
        commands=[]
        def launch(kind,arguments,**kwargs):
            if kind=='session-results':
                return original_launch(kind,arguments,**kwargs)
            key=f'{len(workspace.jobs.entries)+1:032x}'
            folder=workspace.jobs.directory/key/'result'
            folder.mkdir(parents=True)
            command=arguments(folder)
            commands.append(command)
            profile=workspace.profile(kwargs['profile'])
            now=datetime.now(timezone.utc).isoformat()
            report=dict(sweep_type='passive',reported_at=now,expires_at=(datetime.now(timezone.utc)+timedelta(seconds=40)).isoformat())
            if kind=='preflight':
                report.update(ready=True,checked_at=now,profile_revision=profile['revision'],
                    checks=[dict(name='Passive sampling',state='ready',detail='Read-only radio control is ready; no audio connection needed')])
            else:
                self.assertEqual(kind,'record')
                session=workspace.sessions.require(kwargs['survey_session'])
                store=Store(self.root/profile['database'],profile['name'],'mobile','benlink_bluetooth',
                    dict(sweep_type='passive',survey_session=dict(id=session['id'],name=session['name'],notes=session['notes'],sweep_type='passive')))
                try:
                    store.sample(dict(status_received_at=now,status=dict(rssi=42,is_in_tx=False,is_in_rx=True,is_sq=True,is_gps_locked=True,curr_ch_id=1),
                        channel=dict(channel_id=1,rx_freq=146.52,tx_freq=146.52),position=dict(latitude=33.96,longitude=-117.95,time=now,accuracy=8)))
                    report.update(state='recording',samples_recorded=1,recording_session_id=store.session_id,
                        latest_telemetry=dict(received_at=now,rssi=42,is_in_rx=True,is_in_tx=False,gps_locked=True))
                finally:
                    store.close()
            (folder/'report.json').write_text(json.dumps(report))
            workspace.jobs.entries[key]=dict(id=key,kind=kind,profile=profile['id'],survey_session=kwargs['survey_session'],
                title=kwargs['title'],state='completed' if kind=='preflight' else 'running',created_at=now,finished_at=None,
                resources=kwargs['resources'],message='Passive fixture',forced=False,stop_requested=False)
            workspace.jobs._save(workspace.jobs.entries[key])
            return workspace.jobs.describe(key)
        def stop(key):
            workspace.jobs.entries[key].update(state='stopped',finished_at=datetime.now(timezone.utc).isoformat())
            return workspace.jobs.describe(key)
        with patch.object(workspace.jobs,'launch',side_effect=launch),patch.object(workspace.jobs,'stop',side_effect=stop) as halt:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.environ.get('GPS_RSSI_CHROMIUM'),args=['--no-sandbox'])
                page=browser.new_page(viewport=dict(width=1400,height=1000))
                requests=[]
                errors=[]
                page.on('request',lambda request:requests.append(request.url))
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.route(f'http://127.0.0.1:{self.profiles[0]["port"]}/**',lambda route:route.fulfill(body='<html>Passive dashboard fixture</html>',content_type='text/html'))
                def no_audio(route):
                    response=route.fetch()
                    data=response.json()
                    data['capabilities']['audio']=False
                    route.fulfill(response=response,json=data)
                page.route('**/api/state',no_audio)
                page.goto(f'http://127.0.0.1:{self.server.server_port}/#field')
                expect(page.locator('#field-sweep-type')).to_have_value('passive')
                expect(page.locator('#active-sweep-plan')).to_be_hidden()
                expect(page.locator('#field-next-tone-panel')).to_be_hidden()
                page.locator('#new-session').click()
                expect(page.locator('#session-sweep-type')).to_have_value('passive')
                page.locator('#session-name').fill('Ambient route')
                page.locator('#session-form').get_by_role('button',name='Create session',exact=True).click()
                expect(page.locator('#session-dialog')).to_be_hidden()
                expect(page.locator('#field-sweep-type')).to_be_disabled()
                expect(page.locator('#field-session-description')).to_contain_text('PASSIVE SWEEP')
                expect(page.locator('#field-check')).to_be_enabled()
                page.locator('#field-check').click()
                expect(page.locator('#field-start')).to_be_enabled()
                expect(page.locator('#field-start')).to_have_text('Start passive sweep')
                page.locator('#field-start').click()
                expect(page.locator('#transmit-dialog')).to_be_hidden()
                expect(page.locator('#field-phase')).to_have_text('Passive · recording')
                expect(page.locator('#field-rssi')).to_have_text('42.0')
                expect(page.locator('#field-halt')).to_have_text('Stop passive sweep')
                expect(page.locator('#field-tones-fact')).to_be_hidden()
                self.assertEqual(commands[0][-2:],['--sweep-type','passive'])
                self.assertEqual(commands[1][0],'start')
                self.assertNotIn('--transmit',commands[1])
                self.assertNotIn('--automatic',commands[1])
                page.screenshot(path='/workspace/work/passive-sweep.png',full_page=True)
                page.locator('#finish-session').click()
                expect(page.locator('#session-open-results')).to_be_visible(timeout=20000)
                halt.assert_called_once()
                page.reload()
                expect(page.locator('#field-sweep-type')).to_have_value('passive')
                page.locator('[data-view="surveys"]').click()
                card=page.locator('.session-card').filter(has_text='Ambient route')
                expect(card).to_contain_text('passive sweep')
                expect(card).not_to_contain_text('completed tones')
                with page.expect_popup() as popup:
                    card.get_by_role('link',name='Open results',exact=False).click()
                result=popup.value
                result.wait_for_load_state()
                expect(result.locator('body')).to_contain_text('PASSIVE SWEEP RESULTS')
                expect(result.locator('body')).not_to_contain_text('Completed tones')
                result.route('**/tiles/**',lambda route:route.abort())
                result.get_by_role('link',name='Open survey map',exact=True).click()
                expect(result.locator('#survey-title')).to_contain_text('Passive sweep')
                result.close()
                with page.expect_download() as download:
                    card.get_by_role('link',name='Download survey ZIP',exact=True).click()
                target=self.root/'passive.zip'
                download.value.save_as(target)
                with zipfile.ZipFile(target) as archive:
                    summary=json.loads(archive.read('summary.json'))
                    self.assertEqual(summary['sweep_type'],'passive')
                    scoped=self.root/'passive-only.sqlite'
                    scoped.write_bytes(archive.read('recordings.sqlite'))
                self.assertEqual(len(load_log(scoped)[1]),1)
                page.locator('[data-view="field"]').click()
                page.locator('#field-session').select_option('')
                page.locator('#field-sweep-type').select_option('active')
                expect(page.locator('#active-sweep-plan')).to_be_visible()
                expect(page.locator('#field-next-tone-panel')).to_be_visible()
                expect(page.locator('#field-check')).to_be_disabled()
                expect(page.locator('#field-start')).to_be_disabled()
                self.assertFalse(any('/api/prepare-transmit' in url for url in requests))
                for width in (320,768,1440):
                    page.set_viewport_size(dict(width=width,height=900))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width)
                self.assertFalse(errors)
                browser.close()

    def test_recorder_status_recovery_and_saved_readings_are_distinct(self):
        from gps_rssi.operation_status import OperationStatus
        workspace=self.server.workspace
        profile=self.profiles[0]
        key='e'*32
        folder=workspace.jobs.directory/key
        writer=OperationStatus(folder/'result',samples_recorded=0,latest_telemetry=None)
        writer.update(state='connecting',attempt=1,valid_for=75)
        workspace.jobs.entries[key]=dict(id=key,kind='record',profile=profile['id'],title='Recording · '+profile['name'],
            state='running',created_at=datetime.now(timezone.utc).isoformat(),resources=['radio:'+profile['address']],
            message='Working',forced=False,stop_requested=False)
        workspace.jobs._save(workspace.jobs.entries[key])
        def stop(job_id):
            self.assertEqual(job_id,key)
            workspace.jobs.entries[key]['state']='stopping'
            return workspace.jobs.describe(key)
        with patch.object(workspace.jobs,'stop',side_effect=stop) as stopping,patch.object(workspace.jobs,'launch') as launch:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.environ.get('GPS_RSSI_CHROMIUM'),args=['--no-sandbox'])
                page=browser.new_page(viewport=dict(width=1440,height=1000))
                page.route(f'http://127.0.0.1:{profile["port"]}/**',lambda route:route.fulfill(body='<html>Read-only dashboard fixture</html>',content_type='text/html'))
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{self.server.server_port}/#radios')
                card=page.locator('.radio-card[data-profile="'+profile['id']+'"]')
                expect(card.locator('.badge')).to_have_text('Connecting')
                expect(card.locator('.radio-stats small').first).to_have_text('Last recorded RSSI /100')
                expect(card.get_by_role('button',name='Stop recording',exact=True)).to_be_enabled()
                writer.update(state='connected')
                page.evaluate('refresh()')
                expect(card.locator('.badge')).to_have_text('Waiting for telemetry')
                now=datetime.now(timezone.utc).isoformat()
                store=Store(self.root/profile['database'],profile['name'],'mobile','benlink_bluetooth',{})
                try:
                    store.sample(dict(status_received_at=now,status=dict(rssi=50,is_in_tx=False,is_in_rx=True,
                        is_sq=True,is_gps_locked=False,curr_ch_id=1),position=None))
                    writer.update(state='recording',samples_recorded=1,recording_session_id=store.session_id,
                        latest_telemetry=dict(received_at=now,rssi=50,is_in_rx=True,is_in_tx=False,gps_locked=False,gps_recorded=False))
                finally:
                    store.close()
                page.evaluate('refresh()')
                expect(card.locator('.badge')).to_have_text('Recording')
                expect(card.locator('.radio-stats small').first).to_have_text('Receive RSSI /100')
                expect(card.locator('.radio-guidance')).to_contain_text('fresh GPS fix')
                writer.update(state='reconnecting',error='Radio status timed out',error_stage='status',
                    retry_at=(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat())
                page.evaluate('refresh()')
                expect(card.locator('.badge')).to_have_text('Reconnecting')
                expect(card.locator('.radio-stats small').first).to_have_text('Last recorded RSSI /100')
                expect(card.locator('.radio-guidance')).to_contain_text('retries automatically')
                card.get_by_role('button',name='View operation',exact=True).click()
                expect(page.locator('[data-job="'+key+'"] details').last).to_be_visible()
                page.screenshot(path='/workspace/work/radio-recovery.png',full_page=True)
                writer.update(state='recording',error=None,error_stage=None)
                page.evaluate('refresh()')
                expect(card.locator('.badge')).to_have_text('Recording')
                writer.report['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
                (folder/'result/report.json').write_text(json.dumps(writer.report))
                page.evaluate('refresh()')
                expect(card.locator('.badge')).to_have_text('Status stale')
                expect(card.get_by_role('button',name='Stop recording',exact=True)).to_be_enabled()
                page.route('**/api/state',lambda route:route.abort())
                page.evaluate('lastStateReceivedAt=Date.now()-7000;renderRadios();renderJobs()')
                expect(card.locator('.badge')).to_have_text('Status unavailable')
                expect(card.locator('.radio-status-detail')).to_contain_text('unknown')
                page.unroute('**/api/state')
                writer.update(state='recording')
                page.evaluate('refresh()')
                page.locator('[data-view="live"]').click()
                expect(page.locator('#live-status-label')).to_have_text('Recording')
                writer.update(state='reconnecting',error='Radio link lost',error_stage='connect')
                page.evaluate('refresh()')
                expect(page.locator('#live-status-label')).to_have_text('Reconnecting')
                expect(page.locator('#live-status-detail')).to_contain_text('Radio link lost')
                page.locator('[data-view="radios"]').click()
                card.get_by_role('button',name='Stop recording',exact=True).click()
                expect(card.locator('.badge')).to_have_text('Stopping')
                writer.update(state='stopped')
                workspace.jobs.entries[key]['state']='stopped'
                page.evaluate('refresh()')
                expect(card.locator('.badge')).to_have_text('Stopped')
                expect(card.locator('.radio-stats small').first).to_have_text('Last recorded RSSI /100')
                expect(card.get_by_role('button',name='Start recording',exact=True)).to_be_enabled()
                stopping.assert_called_once_with(key)
                launch.assert_not_called()
                self.assertFalse(errors)
                browser.close()

    def test_named_session_finish_and_results_contain_only_this_run(self):
        from gps_rssi.field_recording import field_recording
        from gps_rssi.storage import load_log
        from gps_rssi.survey_sessions import session_path
        workspace=self.server.workspace
        original_launch=workspace.jobs.launch
        launched=[]
        def launch(kind, arguments, **kwargs):
            if kind=='session-results':
                return original_launch(kind, arguments, **kwargs)
            launched.append(kind)
            key=f'{len(workspace.jobs.entries)+1:032x}'
            folder=workspace.jobs.directory/key/'result'
            folder.mkdir(parents=True)
            profile=workspace.profile(kwargs['profile'])
            now=datetime.now(timezone.utc).isoformat()
            if kind=='preflight':
                report=dict(ready=True, checked_at=now, profile_revision=profile['revision'],
                    clock_source='computer', checks=[dict(name='Timing',state='attention',detail='Using computer clock')])
            else:
                report=dict(automatic=True, state='waiting', clock_source='computer', tones_sent=0,
                    latest_telemetry=dict(received_at=now,rssi=50,is_in_rx=True,is_in_tx=False,gps_locked=True))
                with field_recording(self.root/profile['id'],profile['address'],profile['role'],report,
                                     session_path(self.root,kwargs['survey_session'])) as recorder:
                    recorder.store.sample(dict(status_received_at=now,status=dict(rssi=50,is_in_tx=False,is_in_rx=True,
                        is_sq=True,is_gps_locked=True,curr_ch_id=1),channel=dict(channel_id=1,rx_freq=146.52,tx_freq=146.52),
                        position=dict(latitude=33.96,longitude=-117.95,time=now,accuracy=8)))
                    report['samples_recorded']=1
            (folder/'report.json').write_text(json.dumps(report))
            workspace.jobs.entries[key]=dict(id=key,kind=kind,profile=profile['id'],survey_session=kwargs['survey_session'],
                title=kwargs['title'],state='completed' if kind=='preflight' else 'running',created_at=now,
                finished_at=None,resources=kwargs['resources'],message='Test fixture',forced=False,stop_requested=False)
            workspace.jobs._save(workspace.jobs.entries[key])
            return workspace.jobs.describe(key)
        def stop(key):
            report_path=workspace.jobs.directory/key/'result'/'report.json'
            report=json.loads(report_path.read_text())
            report.update(stop_verified=True,stop_attempted=True)
            report_path.write_text(json.dumps(report))
            workspace.jobs.entries[key].update(state='stopped',finished_at=datetime.now(timezone.utc).isoformat())
            workspace.jobs._save(workspace.jobs.entries[key])
            return workspace.jobs.describe(key)
        with patch.object(workspace.jobs,'launch',side_effect=launch),patch.object(workspace.jobs,'stop',side_effect=stop) as halt:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.environ.get('GPS_RSSI_CHROMIUM'),args=['--no-sandbox'])
                page=browser.new_page(viewport=dict(width=1400,height=1000))
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{self.server.server_port}/#field')
                page.locator('#new-session').click()
                page.locator('#session-sweep-type').select_option('active')
                page.locator('#session-name').fill('East ridge antenna test')
                page.locator('#session-notes').fill('Roof antenna at 2 m\nDry conditions')
                page.locator('#session-radio-list input[value="'+self.profiles[2]['id']+'"]').check()
                page.locator('#session-form').get_by_role('button',name='Create session',exact=True).click()
                expect(page.locator('#session-dialog')).to_be_hidden()
                expect(page.locator('#field-session-description')).to_contain_text('East ridge antenna test')
                self.assertEqual(len(launched),0)
                expect(page.locator('#field-radio option')).to_have_count(3)
                page.locator('#field-radio').select_option(self.profiles[0]['id'])
                page.locator('#field-check').click()
                expect(page.locator('#field-start')).to_be_enabled()
                page.locator('#field-start').click()
                expect(page.locator('#transmit-review')).to_contain_text('East ridge antenna test')
                page.locator('#transmit-confirm').check()
                page.locator('#confirm-transmit').click()
                expect(page.locator('#field-samples')).to_have_text('1')
                page.screenshot(path='/workspace/work/named-session-field.png',full_page=True)
                page.locator('#finish-session').click()
                expect(page.locator('#session-open-results')).to_be_visible(timeout=20000)
                expect(page.locator('#field-start')).to_be_disabled()
                halt.assert_called_once()
                self.assertEqual(launched,['preflight','transmit'])
                page.reload()
                expect(page.locator('#field-session-description')).to_contain_text('East ridge antenna test')
                page.locator('[data-view="surveys"]').click()
                card=page.locator('.session-card').filter(has_text='East ridge antenna test')
                expect(card).to_contain_text('Results ready')
                expect(card).to_contain_text('Roof antenna at 2 m')
                with page.expect_popup() as popup:
                    card.get_by_role('link',name='Open results',exact=False).click()
                results=popup.value
                results.wait_for_load_state()
                expect(results.locator('h1')).to_have_text('East ridge antenna test')
                expect(results.locator('dd').first).to_have_text('1')
                results.route('**/tiles/**',lambda route:route.abort())
                with results.expect_navigation() as navigation:
                    results.get_by_role('link',name='Open survey map',exact=True).click()
                self.assertEqual(navigation.value.status,200)
                self.assertIn('map.html',results.url)
                results.close()
                with page.expect_download() as info:
                    card.get_by_role('link',name='Download survey ZIP',exact=True).click()
                target=self.root/'session.zip'
                info.value.save_as(target)
                with zipfile.ZipFile(target) as archive:
                    self.assertIn('survey-session.json',archive.namelist())
                    self.assertIn('index.html',archive.namelist())
                    database=self.root/'session-only.sqlite'
                    database.write_bytes(archive.read('recordings.sqlite'))
                self.assertEqual(len(load_log(database)[1]),1)
                self.assertEqual(len(load_log(self.root/self.profiles[0]['database'])[1]),4)
                for width in (320,768,1440):
                    page.set_viewport_size(dict(width=width,height=900))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width)
                self.assertFalse(errors)
                browser.close()

    def test_field_preflight_recording_monitor_and_halt_without_rf(self):
        workspace = self.server.workspace
        commands = []
        def launch(kind, arguments, **kwargs):
            key = f'{len(workspace.jobs.entries)+1:032x}'
            folder = workspace.jobs.directory/key/'result'
            folder.mkdir(parents=True)
            commands.append(arguments(folder))
            profile = workspace.profile(kwargs['profile'])
            report = (dict(ready=True, checks=[dict(name='Timing', state='attention', detail='Using computer. Synchronize this computer clock.')],
                clock_source='computer', clock_fallback='Radio GPS is not locked', checked_at=datetime.now(timezone.utc).isoformat(), profile_revision=profile['revision'])
                if kind == 'preflight' else dict(automatic=True, recording=True, state='waiting', clock_source='radio GPS',
                    next_tone_at='2026-11-08T12:00:10+00:00', next_tone_host_at=(datetime.now(timezone.utc)+timedelta(seconds=20)).isoformat(),
                    samples_recorded=12, tones_sent=0, latest_telemetry=dict(received_at=datetime.now(timezone.utc).isoformat(),
                    rssi=50, is_in_rx=True, is_in_tx=False, gps_locked=True)))
            (folder/'report.json').write_text(json.dumps(report))
            workspace.jobs.entries[key] = dict(id=key, kind=kind, profile=profile['id'], title=kwargs['title'],
                state='completed' if kind=='preflight' else 'running', created_at=datetime.now(timezone.utc).isoformat(),
                resources=kwargs['resources'], message='Test fixture', stop_requested=False, forced=False)
            return workspace.jobs.describe(key)
        def stop(key):
            workspace.jobs.entries[key]['state']='stopped'
            folder=workspace.jobs.directory/key/'result'
            report=json.loads((folder/'report.json').read_text())
            report['stop_verified']=True
            (folder/'report.json').write_text(json.dumps(report))
            return workspace.jobs.describe(key)
        with patch.object(workspace.jobs, 'launch', side_effect=launch), patch.object(workspace.jobs, 'stop', side_effect=stop) as halt:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True, executable_path=os.environ.get('GPS_RSSI_CHROMIUM'), args=['--no-sandbox'])
                page=browser.new_page(viewport=dict(width=1400, height=1000))
                errors=[]
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{self.server.server_port}/#field')
                page.locator('#field-sweep-type').select_option('active')
                page.locator('#field-radio').select_option(self.profiles[0]['id'])
                expect(page.locator('#field-role')).to_contain_text(':10 / :30 / :50')
                expect(page.locator('#field-start')).to_be_disabled()
                page.locator('#field-check').click()
                expect(page.locator('#field-checks')).to_contain_text('Needs attention')
                expect(page.locator('#field-start')).to_be_enabled()
                page.locator('#field-start').click()
                expect(page.locator('#transmit-review')).to_contain_text('RSSI and enabled GPS between tones')
                page.locator('#transmit-confirm').check()
                page.locator('#confirm-transmit').click()
                expect(page.locator('#field-phase')).to_have_text('Armed · waiting')
                expect(page.locator('#field-countdown')).to_have_text(__import__('re').compile(r'\d+s'))
                expect(page.locator('#field-rssi')).to_have_text('50.0')
                expect(page.locator('#field-samples')).to_have_text('12')
                expect(page.locator('#field-clock')).to_have_text('radio GPS')
                expect(page.locator('#field-clock-reason')).to_have_text('')
                survey_key=next(key for key,entry in workspace.jobs.entries.items() if entry['kind']=='transmit')
                report_path=workspace.jobs.directory/survey_key/'result'/'report.json'
                current_report=json.loads(report_path.read_text())
                current_report['reported_at']=(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
                report_path.write_text(json.dumps(current_report))
                page.evaluate('refresh()')
                expect(page.locator('#field-phase')).to_have_text('Status stale')
                expect(page.locator('#field-countdown')).to_have_text('—')
                current_report['reported_at']=datetime.now(timezone.utc).isoformat()
                report_path.write_text(json.dumps(current_report))
                page.evaluate('refresh()')
                expect(page.locator('#field-phase')).to_have_text('Armed · waiting')
                page.locator('#field-theme').click()
                expect(page.locator('#view-field')).to_have_class(__import__('re').compile('night'))
                page.locator('#field-focus').click()
                expect(page.locator('.sidebar')).to_be_hidden()
                page.screenshot(path='/workspace/work/field-screen.png', full_page=True)
                for width in (320, 768, 1440):
                    page.set_viewport_size(dict(width=width,height=900))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                page.locator('#field-focus').click()
                page.reload()
                expect(page.locator('#field-radio')).to_have_value(self.profiles[0]['id'])
                expect(page.locator('#view-field')).to_have_class(__import__('re').compile('night'))
                self.assertEqual(len(commands), 2)
                page.route('**/api/state', lambda route: route.abort())
                page.evaluate('lastStateReceivedAt=Date.now()-7000;renderField()')
                expect(page.locator('#field-phase')).to_have_text('Connection lost')
                expect(page.locator('#field-countdown')).to_have_text('—')
                expect(page.locator('#field-rssi')).to_have_text('—')
                expect(page.locator('#field-stop-status')).to_have_text('Radio state unknown')
                page.unroute('**/api/state')
                page.evaluate('refresh()')
                expect(page.locator('#field-phase')).to_have_text('Armed · waiting')
                page.locator('#field-halt').click()
                expect(page.locator('#field-phase')).to_have_text('Stopped')
                expect(page.locator('#field-stop-status')).to_have_text('TX verified off')
                expect(page.locator('#field-halt')).to_be_disabled()
                page.evaluate("state.jobs.find(j=>j.kind==='transmit').result.reported_at='2000-01-01T00:00:00Z';renderField()")
                expect(page.locator('#field-start')).to_be_enabled()
                halt.assert_called_once()
                self.assertIn('--record-config', commands[-1])
                self.assertEqual(commands[-1][commands[-1].index('--automatic')+1], 'mobile')
                self.assertFalse(errors)
                browser.close()

    def test_automatic_review_and_halt_without_rf(self):
        workspace = self.server.workspace
        commands = []
        key = "e" * 32
        def launch(kind, arguments, **kwargs):
            commands.append(arguments(self.root / "result"))
            folder = workspace.jobs.directory / key
            (folder / "result").mkdir(parents=True)
            entry = dict(id=key, kind=kind, title=kwargs["title"], profile=kwargs["profile"],
                state="running", created_at=datetime.now(timezone.utc).isoformat(), resources=[],
                message="Armed", stop_requested=False, forced=False)
            workspace.jobs.entries[key] = entry
            (folder / "result" / "report.json").write_text(json.dumps(dict(automatic=True,
                state="waiting", clock_source="computer", clock_fallback="Radio GPS is not locked", tones_sent=0)))
            return workspace.jobs.describe(key)
        def stop(job_id):
            self.assertEqual(job_id, key)
            workspace.jobs.entries[key]["state"] = "stopped"
            return workspace.jobs.describe(key)
        with patch.object(workspace.jobs, "launch", side_effect=launch), patch.object(workspace.jobs, "stop", side_effect=stop) as halt:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get("GPS_RSSI_CHROMIUM"), args=["--no-sandbox"])
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/")
                page.locator('[data-view="audio"]').click()
                page.locator("#automatic-tone").check()
                expect(page.locator("#tone-duration")).to_be_disabled()
                for profile, expected in ((self.profiles[0], ":10 / :30 / :50"), (self.profiles[2], ":00 / :20 / :40")):
                    page.locator("#transmitter-radio").select_option(profile["id"])
                    page.locator("#review-transmit").click()
                    expect(page.locator("#transmit-review")).to_contain_text(expected)
                    expect(page.locator("#transmit-review")).to_contain_text("1000 Hz")
                    expect(page.locator("#confirm-transmit")).to_be_disabled()
                    if profile["role"] == "mobile":
                        page.locator("#transmit-dialog").get_by_role("button", name="Cancel", exact=True).click()
                page.locator("#transmit-confirm").check()
                page.locator("#confirm-transmit").click()
                expect(page.locator("#transmit-banner")).to_be_visible()
                expect(page.locator("#stop-tone-banner")).to_have_text("Halt automatic mode")
                expect(page.locator(".job").filter(has_text="Automatic tones")).to_contain_text("Clock: computer")
                page.locator("#stop-tone-banner").click()
                expect(page.locator("#transmit-banner")).to_be_hidden()
                halt.assert_called_once_with(key)
                self.assertEqual(commands[0][-2:], ["--automatic", "fixed"])
                self.assertFalse(errors)
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
