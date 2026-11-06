"""Rebuild the planning overlay; requires shapely and pyproj only for this script.

Input: Census CitySDK v2/GeoJSON/500k/2022/06/place.json.
Usage: python scripts/build_area.py input.json output.geojson
Output must not exist. No network access is performed by this script.
"""

import hashlib
import json
from pathlib import Path
import sys

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform


def build(source):
    data = json.loads(source.read_bytes())
    matches = [f for f in data["features"] if f["properties"].get("GEOID") == "0639304"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one La Habra Heights feature (GEOID 0639304)")
    city = shape(matches[0]["geometry"])
    if not city.is_valid or city.is_empty:
        raise ValueError("City boundary is invalid")
    forward = Transformer.from_crs("EPSG:4326", "EPSG:32611", always_xy=True).transform
    reverse = Transformer.from_crs("EPSG:32611", "EPSG:4326", always_xy=True).transform
    buffered = transform(reverse, transform(forward, city).buffer(804.672))
    return {
        "type": "FeatureCollection",
        "name": "La Habra Heights + 0.5 mile planning buffer",
        "provenance": {
            "source": "https://github.com/uscensusbureau/citysdk/blob/master/v2/GeoJSON/500k/2022/06/place.json",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "geoid": "0639304", "boundary_year": 2022, "scale": "1:500,000 generalized Census boundary",
            "buffer_miles": 0.5, "buffer_meters": 804.672, "buffer_crs": "EPSG:32611",
            "note": "Planning outline, not a current surveyed or legal city boundary. Buffer is outward from the city polygon, not its center.",
        },
        "features": [
            {"type": "Feature", "properties": {"kind": "buffer", "name": "0.5-mile planning buffer"}, "geometry": mapping(buffered)},
            {"type": "Feature", "properties": {"kind": "city", "name": "La Habra Heights (Census 2022)"}, "geometry": mapping(city)},
        ],
    }


if __name__ == "__main__":
    result = build(Path(sys.argv[1]))
    with Path(sys.argv[2]).open("x", encoding="utf-8") as output:
        json.dump(result, output, separators=(",", ":"))
