#!/bin/bash
# ================================================================
#  Asset Tracker - macOS launcher. Double-click to start.
#  First run creates a private Python environment (needs internet).
# ================================================================
cd "$(dirname "$0")" || exit 1

pause_exit() { echo; read -r -p "Press Enter to close..."; exit "${1:-1}"; }

if [ ! -x .venv/bin/python ]; then
    PY=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && \
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            PY="$candidate"; break
        fi
    done
    if [ -z "$PY" ]; then
        echo "Python 3.9 or newer is required (3.12 recommended)."
        echo "Download the macOS installer from https://www.python.org/downloads/macos/"
        echo "then double-click start-mac.command again."
        pause_exit 1
    fi
    echo "Setting up Asset Tracker for the first time (this takes a minute)..."
    "$PY" -m venv .venv || pause_exit 1
fi

.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || {
    echo "Installing the required packages failed. Check the internet connection (first run only)."
    pause_exit 1
}
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements-reader.txt || \
    echo "WARNING: reader support (pyscard) could not be installed. Smart-card readers and tag programming will not work."

.venv/bin/python run.py --pcsc --open "$@"
echo "Asset Tracker has stopped."
pause_exit 0
