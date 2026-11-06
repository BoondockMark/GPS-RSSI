from contextlib import closing, redirect_stdout
import csv
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from gps_rssi.cli import parser, run
from gps_rssi.gis import FORMATS, GPX, KML, RF, export_points
from gps_rssi.storage import Store, load_log
from gps_rssi.survey import create_survey, render_survey, survey_data
from test_sharing import frame


class SurveyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = []
        self.unsafe = '</script><script>alert("x")</script> & __VISUAL_JS__'
        for station, role, source in ((self.unsafe, "mobile", "benlink_bluetooth"),
                                      ("mobile-b", "mobile", "benlink_bluetooth"),
                                      ("fixed", "fixed", "benlink_bluetooth"),
                                      ("demo", "mobile", "replay")):
            path = self.root / f"{len(self.paths)}.sqlite"
            store = Store(path, station, role, source, {"label": "Roof & antenna <test>"})
            store.sample(frame(0, gps=role == "mobile"))
            store.sample(frame(100, gps=False))
            store.sample(frame(50, tx=True))
            store.event("connected", {})
            store.close()
            self.paths.append(path)

    def test_multi_mobile_bundle_keeps_fixed_history_and_separate_sessions(self):
        output = self.root / "survey"
        before = [p.read_bytes() for p in self.paths]
        summary = create_survey([*self.paths, self.paths[0]], output)
        self.assertEqual(summary["selected_samples"], 9)
        self.assertEqual(summary["receive_observations"], 6)
        self.assertEqual(summary["mapped_points"], 2)
        self.assertEqual(summary["station_count"], 3)
        self.assertEqual(summary["merge"]["duplicate_samples"], 3)
        self.assertEqual(len(load_log(output / "recordings.sqlite")[1]), 12)  # Replay retained for later re-use.
        self.assertEqual([p.read_bytes() for p in self.paths], before)
        self.assertEqual({p.name for p in output.iterdir()}, {"map.html", "summary.json", "recordings.sqlite",
                         "Leaflet-LICENSE.txt", "measurements.csv", *["measurements." + f for f in FORMATS]})
        with (output / "measurements.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 9)
        self.assertNotIn("replay", {r["source"] for r in rows})
        with self.assertRaises(FileExistsError):
            create_survey(self.paths, output)

    def test_source_station_and_gps_filters_exclude_without_fabricating_zero(self):
        points, observations, summary = survey_data(self.paths[0])
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["rssi"], 0)
        self.assertEqual(points[0]["gps_accuracy"], 8)
        self.assertEqual(observations[1]["rssi"], 100)
        self.assertIsNone(observations[1]["lat"])
        self.assertEqual(survey_data(self.paths[0], max_accuracy=5)[0], [])
        self.assertEqual(survey_data(self.paths[0], station="absent")[0], [])
        self.assertEqual(survey_data(self.paths[3])[0], [])
        self.assertEqual(len(survey_data(self.paths[3], source="replay")[0]), 1)
        self.assertEqual(summary["ignored_non_receive"], 1)

    def test_geojson_keeps_longitude_first_identity_zero_and_quality_metadata(self):
        points, _, summary = survey_data(self.paths[0])
        output = self.root / "points.geojson"
        export_points(points, output, "geojson", metadata=summary)
        doc = json.loads(output.read_text())
        feature = doc["features"][0]
        self.assertEqual(feature["geometry"]["coordinates"], [-117.95, 33.96])
        self.assertEqual(feature["properties"]["rssi"], 0)
        self.assertEqual(feature["properties"]["station"], self.unsafe)
        self.assertEqual(feature["id"], points[0]["session"] + ":1")
        self.assertEqual(doc["gps_rssi"]["gps_limits"]["max_accuracy"], 100)
        self.assertNotIn("crs", doc)  # RFC 7946 uses WGS84 by definition.

    def test_kml_and_gpx_escape_labels_and_preserve_metadata_without_invented_tracks(self):
        points, _, _ = survey_data(self.paths[0])
        for format, namespace, tag in (("kml", KML, "Placemark"), ("gpx", GPX, "wpt")):
            with self.subTest(format=format):
                output = self.root / ("points." + format)
                export_points(points, output, format)
                doc = ET.parse(output)
                records = doc.findall(f".//{{{namespace}}}{tag}")
                self.assertEqual(len(records), 1)
                if format == "kml":
                    self.assertEqual(doc.find(f".//{{{KML}}}coordinates").text, "-117.95,33.96")
                    station = doc.find(f".//{{{KML}}}Data[@name='station']/{{{KML}}}value").text
                else:
                    self.assertEqual(records[0].attrib, dict(lat="33.96", lon="-117.95"))
                    station = doc.find(f".//{{{RF}}}station").text
                    self.assertEqual(doc.findall(f".//{{{GPX}}}trk"), [])
                self.assertEqual(station, self.unsafe)

    def test_geopackage_core_metadata_and_binary_geometry_are_correct(self):
        points, _, _ = survey_data(self.paths[0])
        output = self.root / "points.gpkg"
        export_points(points, output, "gpkg")
        with closing(sqlite3.connect(output)) as db:
            self.assertEqual(db.execute("PRAGMA application_id").fetchone()[0], 0x47504B47)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 10300)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("SELECT geometry_type_name,srs_id,z,m FROM gpkg_geometry_columns").fetchone(), ("POINT", 4326, 0, 0))
            geometry, station, rssi = db.execute("SELECT geom,station,rssi FROM measurements").fetchone()
            self.assertEqual(geometry[:4], b"GP\x00\x01")
            self.assertEqual(struct.unpack("<iBIdd", geometry[4:]), (4326, 1, 1, -117.95, 33.96))
            self.assertEqual((station, rssi), (self.unsafe, 0))

    def test_empty_exports_are_valid_and_existing_files_are_untouched(self):
        for format in FORMATS:
            output = self.root / ("empty." + format)
            export_points([], output, format)
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                export_points([], output, format)
            self.assertEqual(output.read_bytes(), before)
        with closing(sqlite3.connect(self.root / "empty.gpkg")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM measurements").fetchone()[0], 0)

    def test_html_embeds_local_map_assets_and_neutralizes_stored_script_tags(self):
        _, observations, summary = survey_data(self.paths[0])
        page = render_survey(observations, summary)
        self.assertNotIn(self.unsafe, page)
        self.assertNotIn('<script src=', page)
        payload = page.split('<script id="survey-data" type="application/json">')[1].split('</script>')[0]
        self.assertEqual(json.loads(payload)["observations"][0]["station"], self.unsafe)
        self.assertIn("Leaflet", page)

    def test_cli_infers_formats_and_preserves_default_all_source_csv(self):
        for format in FORMATS:
            output = self.root / ("cli." + format)
            with redirect_stdout(io.StringIO()):
                run(parser().parse_args(["export", "--db", str(self.paths[0]), "--output", str(output)]))
            self.assertTrue(output.is_file())
        output = self.root / "cli.csv"
        run(parser().parse_args(["export", "--db", str(self.paths[3]), "--output", str(output)]))
        with output.open(newline="") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 3)

    def test_cli_share_merge_and_survey_work_without_bluetooth(self):
        snapshot, merged, bundle = self.root / "snapshot.sqlite", self.root / "merged.sqlite", self.root / "bundle"
        with redirect_stdout(io.StringIO()):
            for argv in (["share", "--db", str(self.paths[0]), "--output", str(snapshot)],
                         ["merge", "--db", str(snapshot), "--db", str(self.paths[1]), "--output", str(merged)],
                         ["survey", "--db", str(merged), "--db", str(snapshot), "--output", str(bundle)]):
                run(parser().parse_args(argv))
        summary = json.loads((bundle / "summary.json").read_text())
        self.assertEqual(summary["mapped_points"], 2)

    def test_failed_bundle_is_not_published_and_staging_is_removed(self):
        output = self.root / "failed"
        with patch("gps_rssi.survey.export_points", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                create_survey(self.paths, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".gps-rssi-survey-*")), [])


if __name__ == "__main__":
    unittest.main()
