#!/usr/bin/env sh
# Start bot: print startup checks, then run in background.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PID_FILE="${SCRIPT_DIR}/.bot.pid"
LOG_FILE="${SCRIPT_DIR}/bot.log"

echo "=== Startup checks ==="

if [ -d "venv" ]; then
    . venv/bin/activate
    echo "  ✓ Using venv: venv/"
elif [ -d ".venv" ]; then
    . .venv/bin/activate
    echo "  ✓ Using venv: .venv/"
else
    echo "  ○ No venv found, using system Python"
fi

if [ -f "requirements.txt" ]; then
    echo "  Checking dependencies..."
    pip install -q -r requirements.txt
    echo "  ✓ Dependencies OK"
fi

if [ -f ".bot.pid" ]; then
    OLD_PID=$(cat ".bot.pid")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "  ⚠ Bot already running (PID $OLD_PID). Run ./stop.sh first."
        exit 1
    fi
    rm -f ".bot.pid"
fi

echo "=== Starting bot in background ==="
BOT_ENTRY="${BOT_ENTRY:-}"
if [ -z "$BOT_ENTRY" ]; then
    BOT_ENTRY="$(python3 - <<'PY'
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
platform = os.environ.get("BOT_PLATFORM", "").strip().lower()
discord = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
telegram = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if platform == "telegram" or (telegram and not discord):
    print("main_telegram.py")
else:
    print("main.py")
PY
)"
fi
nohup python3 "$BOT_ENTRY" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "  ✓ Bot started (PID $(cat "$PID_FILE"), entry: $BOT_ENTRY). Logs: $LOG_FILE"
echo "  Run ./stop.sh to stop."
