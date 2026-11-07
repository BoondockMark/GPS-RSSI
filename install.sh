#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$PROJECT_DIR/scripts/install.py" "$@"
fi
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/install.py" "$@"
fi
echo "Python 3.11 or later is required. Install Python and python3-venv, then rerun this installer." >&2
exit 1
