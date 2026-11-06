#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$PROJECT_DIR/scripts/run-gps-rssi.sh" scan --select "$@"
