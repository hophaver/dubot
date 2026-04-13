#!/usr/bin/env sh
# Start bot: print startup checks, then run in background.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PID_FILE="${SCRIPT_DIR}/.bot.pid"
PIDS_FILE="${SCRIPT_DIR}/.bot.pids"
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

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "  ⚠ Bot already running (PID $OLD_PID). Run ./stop.sh first."
        exit 1
    fi
    rm -f "$PID_FILE"
fi

if [ -f "$PIDS_FILE" ]; then
    if rg -n "^[^:]+:[0-9]+$" "$PIDS_FILE" >/dev/null 2>&1; then
        while IFS=: read -r _name _pid; do
            if [ -n "$_pid" ] && kill -0 "$_pid" 2>/dev/null; then
                echo "  ⚠ Bot already running (PID $_pid). Run ./stop.sh first."
                exit 1
            fi
        done < "$PIDS_FILE"
    fi
    rm -f "$PIDS_FILE"
fi

echo "=== Starting bot in background ==="
PLATFORM_DECISION="$(python3 - <<'PY'
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
platform = os.environ.get("BOT_PLATFORM", "").strip().lower()
discord = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
telegram = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if platform in {"discord", "telegram", "both"}:
    print(platform)
elif discord and telegram:
    print("both")
elif telegram:
    print("telegram")
else:
    print("discord")
PY
)"

if [ "$PLATFORM_DECISION" = "both" ]; then
    nohup env DUBOT_RUNTIME=discord python3 "main.py" >> "$LOG_FILE" 2>&1 &
    DISCORD_PID=$!
    nohup env DUBOT_RUNTIME=telegram python3 "main_telegram.py" >> "$LOG_FILE" 2>&1 &
    TELEGRAM_PID=$!
    printf "discord:%s\ntelegram:%s\n" "$DISCORD_PID" "$TELEGRAM_PID" > "$PIDS_FILE"
    echo "  ✓ Discord started (PID $DISCORD_PID)"
    echo "  ✓ Telegram started (PID $TELEGRAM_PID)"
    echo "  ✓ Multi-platform mode active. Logs: $LOG_FILE"
elif [ "$PLATFORM_DECISION" = "telegram" ]; then
    nohup env DUBOT_RUNTIME=telegram python3 "main_telegram.py" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "  ✓ Telegram started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
else
    nohup env DUBOT_RUNTIME=discord python3 "main.py" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "  ✓ Discord started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
fi
echo "  Run ./stop.sh to stop."
