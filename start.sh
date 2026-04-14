#!/usr/bin/env sh
# Start bot: print startup checks, then run in background.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PID_FILE="${SCRIPT_DIR}/.bot.pid"
PIDS_FILE="${SCRIPT_DIR}/.bot.pids"
LOG_FILE="${SCRIPT_DIR}/bot.log"

print_recent_log() {
    if [ -f "$LOG_FILE" ]; then
        echo "  --- Last log lines ---"
        "$PYTHON_BIN" - <<'PY'
import os
path = os.environ.get("DUBOT_LOG_PATH", "")
if not path or not os.path.isfile(path):
    raise SystemExit(0)
with open(path, "r", errors="ignore") as f:
    lines = f.readlines()[-40:]
for line in lines:
    print("  " + line.rstrip())
PY
        echo "  --- End log ---"
    fi
}

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

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "  ❌ Python is not available in PATH."
    exit 1
fi
echo "  ✓ Python: $PYTHON_BIN"

if [ -f "requirements.txt" ]; then
    echo "  Checking dependencies..."
    if "$PYTHON_BIN" -m pip install -q -r requirements.txt; then
        echo "  ✓ Dependencies OK"
    elif "$PYTHON_BIN" -m pip install -q --user -r requirements.txt; then
        echo "  ✓ Dependencies OK (user site)"
    else
        echo "  ⚠ Dependency install failed; continuing startup."
        echo "    Run manually: $PYTHON_BIN -m pip install -r requirements.txt"
    fi
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
PLATFORM_DECISION="$("$PYTHON_BIN" - <<'PY'
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
    print("none")
PY
)"

if [ "$PLATFORM_DECISION" = "none" ]; then
    echo "  ❌ No bot token found."
    echo "  Set DISCORD_BOT_TOKEN and/or TELEGRAM_BOT_TOKEN in .env, then retry."
    exit 1
fi

if [ "$PLATFORM_DECISION" = "both" ]; then
    nohup env DUBOT_RUNTIME=discord "$PYTHON_BIN" "main.py" >> "$LOG_FILE" 2>&1 &
    DISCORD_PID=$!
    nohup env DUBOT_RUNTIME=telegram "$PYTHON_BIN" "main_telegram.py" >> "$LOG_FILE" 2>&1 &
    TELEGRAM_PID=$!
    printf "discord:%s\ntelegram:%s\n" "$DISCORD_PID" "$TELEGRAM_PID" > "$PIDS_FILE"
    sleep 2
    DISCORD_OK=0
    TELEGRAM_OK=0
    if kill -0 "$DISCORD_PID" 2>/dev/null; then DISCORD_OK=1; fi
    if kill -0 "$TELEGRAM_PID" 2>/dev/null; then TELEGRAM_OK=1; fi
    if [ "$DISCORD_OK" -eq 1 ] && [ "$TELEGRAM_OK" -eq 1 ]; then
        echo "  ✓ Discord started (PID $DISCORD_PID)"
        echo "  ✓ Telegram started (PID $TELEGRAM_PID)"
        echo "  ✓ Multi-platform mode active. Logs: $LOG_FILE"
    else
        rm -f "$PIDS_FILE"
        echo "  ❌ One or more bot runtimes failed to start. Check logs: $LOG_FILE"
        DUBOT_LOG_PATH="$LOG_FILE" print_recent_log
        exit 1
    fi
elif [ "$PLATFORM_DECISION" = "telegram" ]; then
    nohup env DUBOT_RUNTIME=telegram "$PYTHON_BIN" "main_telegram.py" >> "$LOG_FILE" 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > "$PID_FILE"
    sleep 2
    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "  ✓ Telegram started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
    else
        rm -f "$PID_FILE"
        echo "  ❌ Telegram failed to start. Check logs: $LOG_FILE"
        DUBOT_LOG_PATH="$LOG_FILE" print_recent_log
        exit 1
    fi
else
    nohup env DUBOT_RUNTIME=discord "$PYTHON_BIN" "main.py" >> "$LOG_FILE" 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > "$PID_FILE"
    sleep 2
    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "  ✓ Discord started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
    else
        rm -f "$PID_FILE"
        echo "  ❌ Discord failed to start. Check logs: $LOG_FILE"
        DUBOT_LOG_PATH="$LOG_FILE" print_recent_log
        exit 1
    fi
fi
echo "  Run ./stop.sh to stop."
