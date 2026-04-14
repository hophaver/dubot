#!/usr/bin/env sh
# Start bot: print startup checks, then run in background.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PID_FILE="${SCRIPT_DIR}/.bot.pid"
PIDS_FILE="${SCRIPT_DIR}/.bot.pids"
LOG_FILE="${SCRIPT_DIR}/bot.log"
DISCORD_LOG_FILE="${SCRIPT_DIR}/bot-discord.log"
TELEGRAM_LOG_FILE="${SCRIPT_DIR}/bot-telegram.log"

print_recent_log() {
    if [ -f "$LOG_FILE" ]; then
        echo "  --- Last log lines ---"
        "$PYTHON_BIN" - <<'PY'
import os
import re
path = os.environ.get("DUBOT_LOG_PATH", "")
if not path or not os.path.isfile(path):
    raise SystemExit(0)
with open(path, "r", errors="ignore") as f:
    lines = f.readlines()[-40:]
for line in lines:
    text = line.rstrip("\n")
    # Redact Telegram/Discord-style token patterns in diagnostic output.
    text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED_TOKEN]", text)
    print("  " + text.rstrip())
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
import re
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

def _parse_env_file(path: str):
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
                if not m:
                    continue
                key, val = m.group(1), m.group(2).strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                out[key] = val
    except Exception:
        return {}
    return out

platform = os.environ.get("BOT_PLATFORM", "").strip().lower()
dotenv_vals = _parse_env_file(".env")
if not platform:
    platform = str(dotenv_vals.get("BOT_PLATFORM", "")).strip().lower()
discord = (os.environ.get("DISCORD_BOT_TOKEN", "") or dotenv_vals.get("DISCORD_BOT_TOKEN", "")).strip()
telegram = (os.environ.get("TELEGRAM_BOT_TOKEN", "") or dotenv_vals.get("TELEGRAM_BOT_TOKEN", "")).strip()
if platform in {"discord", "telegram", "both"}:
    print(platform)
elif discord and telegram:
    print("both")
elif telegram:
    print("telegram")
elif discord:
    print("discord")
else:
    print("none")
PY
)"

if [ "$PLATFORM_DECISION" = "none" ]; then
    echo "  ⚠ Could not detect bot token in preflight."
    echo "  Attempting Discord startup anyway (check bot.log if it fails)."
    PLATFORM_DECISION="discord"
fi

if [ "$PLATFORM_DECISION" = "both" ]; then
    nohup env DUBOT_RUNTIME=discord "$PYTHON_BIN" "main.py" >> "$DISCORD_LOG_FILE" 2>&1 &
    DISCORD_PID=$!
    nohup env DUBOT_RUNTIME=telegram "$PYTHON_BIN" "main_telegram.py" >> "$TELEGRAM_LOG_FILE" 2>&1 &
    TELEGRAM_PID=$!
    sleep 4
    DISCORD_OK=0
    TELEGRAM_OK=0
    if kill -0 "$DISCORD_PID" 2>/dev/null; then DISCORD_OK=1; fi
    if kill -0 "$TELEGRAM_PID" 2>/dev/null; then TELEGRAM_OK=1; fi

    if [ "$DISCORD_OK" -eq 1 ] || [ "$TELEGRAM_OK" -eq 1 ]; then
        : > "$PIDS_FILE"
        if [ "$DISCORD_OK" -eq 1 ]; then
            printf "discord:%s\n" "$DISCORD_PID" >> "$PIDS_FILE"
            echo "  ✓ Discord started (PID $DISCORD_PID) · Log: $DISCORD_LOG_FILE"
        else
            echo "  ⚠ Discord failed to start."
            DUBOT_LOG_PATH="$DISCORD_LOG_FILE" print_recent_log
        fi
        if [ "$TELEGRAM_OK" -eq 1 ]; then
            printf "telegram:%s\n" "$TELEGRAM_PID" >> "$PIDS_FILE"
            echo "  ✓ Telegram started (PID $TELEGRAM_PID) · Log: $TELEGRAM_LOG_FILE"
        else
            echo "  ⚠ Telegram failed to start."
            DUBOT_LOG_PATH="$TELEGRAM_LOG_FILE" print_recent_log
        fi
        echo "  ✓ Startup completed with available runtime(s)."
    else
        rm -f "$PIDS_FILE"
        echo "  ❌ Both Discord and Telegram failed to start."
        DUBOT_LOG_PATH="$DISCORD_LOG_FILE" print_recent_log
        DUBOT_LOG_PATH="$TELEGRAM_LOG_FILE" print_recent_log
        exit 1
    fi
elif [ "$PLATFORM_DECISION" = "telegram" ]; then
    nohup env DUBOT_RUNTIME=telegram "$PYTHON_BIN" "main_telegram.py" >> "$TELEGRAM_LOG_FILE" 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > "$PID_FILE"
    sleep 4
    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "  ✓ Telegram started (PID $(cat "$PID_FILE")). Log: $TELEGRAM_LOG_FILE"
    else
        rm -f "$PID_FILE"
        echo "  ❌ Telegram failed to start. Check log: $TELEGRAM_LOG_FILE"
        DUBOT_LOG_PATH="$TELEGRAM_LOG_FILE" print_recent_log
        exit 1
    fi
else
    nohup env DUBOT_RUNTIME=discord "$PYTHON_BIN" "main.py" >> "$DISCORD_LOG_FILE" 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > "$PID_FILE"
    sleep 4
    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "  ✓ Discord started (PID $(cat "$PID_FILE")). Log: $DISCORD_LOG_FILE"
    else
        rm -f "$PID_FILE"
        echo "  ❌ Discord failed to start. Check log: $DISCORD_LOG_FILE"
        DUBOT_LOG_PATH="$DISCORD_LOG_FILE" print_recent_log
        exit 1
    fi
fi
echo "  Run ./stop.sh to stop."
