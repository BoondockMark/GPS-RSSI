#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
GPS_RSSI_PYTHON="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$GPS_RSSI_PYTHON" ]; then
    echo "The project virtual environment was not found." >&2
    echo "See README.md for installation, then try the launcher again." >&2
    exit 1
fi
cd -- "$PROJECT_DIR"
exec "$GPS_RSSI_PYTHON" -m gps_rssi.cli "$@"
