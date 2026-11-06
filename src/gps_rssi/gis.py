"""Dependency-free GIS point exports in WGS84; never interpolate coverage."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
from xml.etree import ElementTree as ET

from .measurements import timestamp, utc_now


FORMATS = ("geojson", "kml", "gpx", "gpkg")
KML = "http://www.opengis.net/kml/2.2"
GPX = "http://www.topografix.com/GPX/1/1"
RF = "https://gps-rssi.local/ns/1"
WGS84 = ('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
         'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]')


def properties(point):
    return {key: value for key, value in point.items() if key not in ("lat", "lon")}


def feature_id(point):
    return f"{point['session']}:{point.get('original_sample', point['id'])}"


def geojson(points, metadata=None):
    return dict(type="FeatureCollection", gps_rssi=metadata or {}, features=[
        dict(type="Feature", id=feature_id(p), geometry=dict(type="Point", coordinates=[p["lon"], p["lat"]]),
             properties=properties(p)) for p in points])


def xml_document(points, format, metadata):
    namespace = KML if format == "kml" else GPX
    ET.register_namespace("", namespace)
    ET.register_namespace("rf", RF)
    def add(parent, tag, value=None, **attributes):
        child = ET.SubElement(parent, "{" + namespace + "}" + tag, attributes)
        if value is not None:
            child.text = str(value)
        return child
    root = ET.Element("{" + namespace + "}" + ("kml" if format == "kml" else "gpx"))
    if format == "gpx":
        root.set("version", "1.1")
        root.set("creator", "GPS-RSSI")
        add(add(root, "metadata"), "desc", json.dumps(metadata, ensure_ascii=False))
        for p in points:
            waypoint = add(root, "wpt", lat=str(p["lat"]), lon=str(p["lon"]))
            add(waypoint, "time", timestamp(p["time"]).isoformat())
            add(waypoint, "name", f"{p['station']} · RSSI {p['rssi']}")
            add(waypoint, "desc", f"Relative RSSI /100; session {p['session']}; {p['source']}")
            extensions = add(waypoint, "extensions")
            for key, value in properties(p).items():
                ET.SubElement(extensions, "{" + RF + "}" + key).text = str(value)
    else:
        document = add(root, "Document")
        add(document, "name", "GPS-RSSI receive measurements")
        add(document, "description", json.dumps(metadata, ensure_ascii=False))
        for p in points:
            placemark = add(document, "Placemark")
            add(placemark, "name", f"{p['station']} · RSSI {p['rssi']}")
            add(add(placemark, "TimeStamp"), "when", timestamp(p["time"]).isoformat())
            extended = add(placemark, "ExtendedData")
            for key, value in properties(p).items():
                add(add(extended, "Data", name=key), "value", value)
            add(add(placemark, "Point"), "coordinates", f"{p['lon']},{p['lat']}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _geopackage(points, output, metadata):
    """GeoPackage 1.3 core point features, with portable binary WKB geometry."""
    with closing(sqlite3.connect(output)) as db, db:
        db.execute("PRAGMA application_id=1196444487")  # GPKG
        db.execute("PRAGMA user_version=10300")
        db.executescript("""
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
                organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL, description TEXT);
            CREATE TABLE gpkg_contents (
                table_name TEXT PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE,
                description TEXT DEFAULT '', last_change DATETIME NOT NULL,
                min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
                srs_id INTEGER REFERENCES gpkg_spatial_ref_sys(srs_id));
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL REFERENCES gpkg_contents(table_name), column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL REFERENCES gpkg_spatial_ref_sys(srs_id),
                z TINYINT NOT NULL, m TINYINT NOT NULL, PRIMARY KEY(table_name,column_name));
            CREATE TABLE measurements (
                fid INTEGER PRIMARY KEY AUTOINCREMENT, geom POINT NOT NULL,
                feature_id TEXT NOT NULL UNIQUE, station TEXT NOT NULL, session TEXT NOT NULL,
                sample INTEGER NOT NULL, source TEXT NOT NULL, time TEXT NOT NULL,
                rssi DOUBLE NOT NULL, rssi_unit TEXT NOT NULL, properties_json TEXT NOT NULL);
        """)
        db.executemany("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)", [
            ("Undefined Cartesian", -1, "NONE", -1, "undefined", "Undefined Cartesian coordinate reference system"),
            ("Undefined geographic", 0, "NONE", 0, "undefined", "Undefined geographic coordinate reference system"),
            ("WGS 84", 4326, "EPSG", 4326, WGS84, "Longitude/latitude in degrees"),
        ])
        xs, ys = [p["lon"] for p in points], [p["lat"] for p in points]
        bounds = (min(xs), min(ys), max(xs), max(ys)) if points else (None,) * 4
        db.execute("INSERT INTO gpkg_contents VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("measurements", "features", "Receive measurements", json.dumps(metadata), utc_now(), *bounds, 4326))
        db.execute("INSERT INTO gpkg_geometry_columns VALUES ('measurements','geom','POINT',4326,0,0)")
        for p in points:
            geometry = b"GP\x00\x01" + struct.pack("<iBIdd", 4326, 1, 1, p["lon"], p["lat"])
            db.execute("INSERT INTO measurements VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
                       (geometry, feature_id(p), p["station"], p["session"], p.get("original_sample", p["id"]),
                        p["source"], p["time"], p["rssi"], "benshi_relative_0_100",
                        json.dumps(properties(p), allow_nan=False)))


def export_points(points, output, format, *, metadata=None):
    if format not in FORMATS:
        raise ValueError(f"Unsupported GIS format: {format}")
    output = Path(output)
    metadata = {"crs": "EPSG:4326", "rssi_unit": "benshi_relative_0_100",
                "meaning": "Measured mobile receive points; transmitter identity and voice coverage unverified.",
                **(metadata or {})}
    # Serialize before creating a destination; the caller supplies qualified points.
    content = None
    if format == "geojson":
        content = json.dumps(geojson(points, metadata), indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
    elif format in ("kml", "gpx"):
        content = xml_document(points, format, metadata)
    created = False
    try:
        with output.open("xb") as stream:
            created = True
            if content is not None:
                stream.write(content)
        if format == "gpkg":
            _geopackage(points, output, metadata)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return len(points)
