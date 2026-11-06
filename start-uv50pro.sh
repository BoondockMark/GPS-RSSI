#!/bin/sh
# Compatibility launcher; use start-fixed.sh for any supported radio.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$PROJECT_DIR/start-fixed.sh" "$@"
