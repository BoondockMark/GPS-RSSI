"""Shared provider catalog and portable browser script, without server imports."""

from importlib.resources import files
import json

SOURCES = json.loads(files("gps_rssi").joinpath("assets/basemaps.json").read_text(encoding="utf-8"))


def basemap_script():
    return files("gps_rssi").joinpath("assets/basemap.js").read_text(encoding="utf-8").replace(
        "__BASEMAP_SOURCES__", json.dumps(SOURCES))
