"""On-demand OSM tiles for sandboxed workspace reports."""

from pathlib import Path
from email.utils import parsedate_to_datetime
import re
import threading
import time
from urllib.request import Request, urlopen

OSM_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "GPS-RSSI/0.1 (+https://github.com/BoondockMark/GPS-RSSI)"
CACHE_SECONDS = 7 * 24 * 60 * 60
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class TileCache:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()

    def get(self, coordinates):
        match = re.fullmatch(r"(\d{1,2})/(\d{1,6})/(\d{1,6})\.png", coordinates)
        if not match:
            raise ValueError("Invalid tile coordinates")
        z, x, y = map(int, match.groups())
        if not (0 <= z <= 19 and 0 <= x < 2**z and 0 <= y < 2**z):
            raise ValueError("Invalid tile coordinates")
        path = self.directory / str(z) / str(x) / f"{y}.png"
        expiry = path.with_suffix(".expires")
        # Serialize misses to avoid concurrent duplicate requests; never prefetch.
        with self.lock:
            if path.exists() and expiry.exists() and time.time() < float(expiry.read_text()):
                return path.read_bytes()
            request = Request(OSM_TEMPLATE.format(z=z, x=x, y=y), headers={
                "User-Agent": USER_AGENT, "Accept": "image/png"})
            with urlopen(request, timeout=15) as response:
                data = response.read(1024 * 1024 + 1)
                if not data.startswith(PNG_SIGNATURE) or len(data) > 1024 * 1024:
                    raise OSError("Tile service did not return a PNG image")
                cache_control = response.headers.get("Cache-Control", "")
                max_age = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control)
                lifetime = CACHE_SECONDS
                if max_age:
                    lifetime = max(0, int(max_age[1]) - int(response.headers.get("Age", "0")))
                elif response.headers.get("Expires"):
                    lifetime = max(0, parsedate_to_datetime(response.headers["Expires"]).timestamp() - time.time())
                if "no-store" in cache_control or "no-cache" in cache_control:
                    lifetime = 0
            if lifetime <= 0:
                return data
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
            expiry.write_text(str(time.time() + lifetime))
            return data
