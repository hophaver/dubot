#!/usr/bin/env sh
# Stop the bot: kill process from .bot.pid and remove PID file.
# (No venv to deactivate in this script; the bot process is stopped.)
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PID_FILE="${SCRIPT_DIR}/.bot.pid"
PIDS_FILE="${SCRIPT_DIR}/.bot.pids"

stop_one() {
    NAME="$1"
    PID="$2"
    if [ -z "$PID" ]; then
        return
    fi
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "$NAME process $PID not running (already stopped)."
        return
    fi
    echo "Stopping $NAME (PID $PID)..."
    kill -TERM "$PID" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "  ✓ $NAME stopped."
            return
        fi
        sleep 1
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "  Forcing $NAME shutdown..."
        kill -KILL "$PID" 2>/dev/null || true
    fi
    echo "  ✓ $NAME stopped."
}

if [ -f "$PIDS_FILE" ]; then
    while IFS=: read -r NAME PID; do
        stop_one "${NAME:-bot}" "$PID"
    done < "$PIDS_FILE"
    rm -f "$PIDS_FILE"
    rm -f "$PID_FILE"
    exit 0
fi

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found (.bot.pid/.bot.pids). Bot may not be running."
    exit 0
fi

PID=$(cat "$PID_FILE")
rm -f "$PID_FILE"
stop_one "bot" "$PID"
