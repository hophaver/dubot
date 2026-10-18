#!/usr/bin/env sh
# Stop the bot: SIGTERM the process from .bot.pid (it saves state on shutdown), force-kill if it hangs.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PID_FILE="${SCRIPT_DIR}/.bot.pid"
DISCORD_LOG_FILE="${SCRIPT_DIR}/bot-discord.log"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found (.bot.pid). Bot may not be running."
    exit 0
fi

PID=$(cat "$PID_FILE")
rm -f "$PID_FILE"
if ! kill -0 "$PID" 2>/dev/null; then
    echo "Bot process $PID not running (already stopped)."
    exit 0
fi
echo "Stopping bot (PID $PID)..."
kill -TERM "$PID" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "  ✓ Bot stopped."
        echo "Log: $DISCORD_LOG_FILE"
        exit 0
    fi
    sleep 1
done
echo "  Forcing shutdown..."
kill -KILL "$PID" 2>/dev/null || true
echo "  ✓ Bot stopped."
echo "Log: $DISCORD_LOG_FILE"
