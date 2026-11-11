#!/bin/bash
# Asset Tracker - Linux launcher. Needs: python3-venv, pcscd (for smart-card readers).
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || python3 -m venv .venv || { echo "Install python3-venv first."; exit 1; }
.venv/bin/python -m pip install -q -r requirements.txt || exit 1
.venv/bin/python -m pip install -q -r requirements-reader.txt || \
    echo "WARNING: pyscard not installed (sudo apt install libpcsclite-dev swig pcscd, then rerun)."
exec .venv/bin/python run.py --pcsc --open "$@"
