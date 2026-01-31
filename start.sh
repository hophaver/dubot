#!/usr/bin/env sh
# Start bot: optional venv, install deps, run main.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d "venv" ]; then
    . venv/bin/activate
elif [ -d ".venv" ]; then
    . .venv/bin/activate
fi

if [ -f "requirements.txt" ]; then
    pip install -q -r requirements.txt
fi

exec python3 main.py
