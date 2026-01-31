#!/usr/bin/env sh
# Stop the bot: kill process from .bot.pid and remove PID file.
# (No venv to deactivate in this script; the bot process is stopped.)
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PID_FILE="${SCRIPT_DIR}/.bot.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found (.bot.pid). Bot may not be running."
    exit 0
fi

PID=$(cat "$PID_FILE")
rm -f "$PID_FILE"

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Process $PID not running (already stopped)."
    exit 0
fi

echo "Stopping bot (PID $PID)..."
kill -TERM "$PID" 2>/dev/null || true
# Give it a moment to shut down gracefully
for _ in 1 2 3 4 5; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "  ✓ Bot stopped."
        exit 0
    fi
    sleep 1
done
# Force kill if still running
if kill -0 "$PID" 2>/dev/null; then
    echo "  Forcing shutdown..."
    kill -KILL "$PID" 2>/dev/null || true
fi
echo "  ✓ Bot stopped."
