import os
import datetime
import json
import requests
import threading
import time
import sys
import re

# Try to load from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded environment variables from .env file")
except ImportError:
    print("⚠️  python-dotenv not installed, using system environment variables")
    pass

# Bot credentials and API keys
def _read_dotenv_values(path: str = ".env"):
    values = {}
    env_path = path
    if not os.path.isabs(env_path):
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.isfile(env_path):
        return values
    try:
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
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
                else:
                    # Strip inline comments in unquoted dotenv values: KEY=value # comment
                    if " #" in val:
                        val = val.split(" #", 1)[0].rstrip()
                values[key] = val
    except Exception:
        return {}
    return values


_DOTENV_VALUES = _read_dotenv_values()


def _normalize_secret(value: str) -> str:
    v = str(value or "").strip().strip('"').strip("'")
    # Remove common invisible/control characters from copy-pasted secrets.
    v = v.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    v = "".join(ch for ch in v if ch.isprintable())
    # Remove all whitespace characters that can sneak in from copy/paste.
    v = re.sub(r"\s+", "", v)
    if v.lower() in {"none", "null"}:
        return ""
    upper = v.upper()
    # Treat common template placeholders as unset so fallback keys can be used.
    if upper in {
        "TOKEN",
        "YOUR_TOKEN",
        "YOUR_API_KEY",
        "API_KEY",
        "CHANGE_ME",
        "REPLACE_ME",
        "OPENROUTER_API_KEY",
        "OPENROUTER_CHAT_API_KEY",
        "OPENROUTER_MANAGEMENT_API_KEY",
    }:
        return ""
    return v


def _env_bool(key: str, default: bool = True) -> bool:
    raw = _DOTENV_VALUES.get(key)
    if raw is None or str(raw).strip() == "":
        raw = os.environ.get(key, "")
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "y")


def _env_raw(key: str) -> str:
    """Single env value for non-secret strings (e.g. model names); strip quotes only, keep spaces/slashes."""
    if key in _DOTENV_VALUES:
        raw = _DOTENV_VALUES.get(key, "")
    else:
        raw = os.environ.get(key, "")
    s = str(raw or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    if s.lower() in ("none", "null"):
        return ""
    return s


def _get_secret(*keys: str) -> str:
    # Prefer .env values on host to avoid stale shell env values.
    for key in keys:
        if key in _DOTENV_VALUES:
            v = _normalize_secret(_DOTENV_VALUES.get(key, ""))
            if v:
                return v
    for key in keys:
        v = _normalize_secret(os.environ.get(key, ""))
        if v:
            return v
    return ""


DISCORD_BOT_TOKEN = _get_secret("DISCORD_BOT_TOKEN")
TELEGRAM_BOT_TOKEN = _get_secret("TELEGRAM_BOT_TOKEN")
HA_URL = _normalize_secret(_DOTENV_VALUES.get("HA_URL", "") or os.environ.get("HA_URL", "")) or 'http://192.168.0.149:8123'
HA_ACCESS_TOKEN = _get_secret("HA_ACCESS_TOKEN")
OLLAMA_URL = _normalize_secret(_DOTENV_VALUES.get("OLLAMA_URL", "") or os.environ.get("OLLAMA_URL", "")) or 'http://localhost:11434'

# /himas: try Home Assistant Assist (conversation API) first; LLM fallback uses HIMAS_PARSE_* below.
HIMAS_ASSIST_ENABLED = _env_bool("HIMAS_ASSIST_ENABLED", True)
HIMAS_ASSIST_LANGUAGE = (
    _normalize_secret(_DOTENV_VALUES.get("HIMAS_ASSIST_LANGUAGE", "") or os.environ.get("HIMAS_ASSIST_LANGUAGE", ""))
    or "en"
)
HIMAS_ASSIST_AGENT_ID = _env_raw("HIMAS_ASSIST_AGENT_ID")
# auto = same as today (user's preferred Ollama model from data/models.json); ollama | openrouter = fixed backend.
HIMAS_PARSE_PROVIDER = (
    (_DOTENV_VALUES.get("HIMAS_PARSE_PROVIDER") or os.environ.get("HIMAS_PARSE_PROVIDER") or "auto")
    .strip()
    .lower()
)
HIMAS_PARSE_MODEL = _env_raw("HIMAS_PARSE_MODEL")
# OpenRouter keys:
# - OPENROUTER_API_KEY is the primary key for chat/completions.
# - OPENROUTER_CHAT_API_KEY and OPENROUTER_MANAGEMENT_API_KEY are optional aliases.
OPENROUTER_API_KEY = _get_secret(
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_APIKEY",
)
OPENROUTER_LEGACY_API_KEY = OPENROUTER_API_KEY
OPENROUTER_CHAT_API_KEY = _get_secret("OPENROUTER_CHAT_API_KEY") or OPENROUTER_API_KEY
OPENROUTER_MANAGEMENT_API_KEY = _get_secret("OPENROUTER_MANAGEMENT_API_KEY") or OPENROUTER_API_KEY
# Cursor user key (preferred var for /cursor spend check attempts)
CURSOR_USER_API_KEY = _get_secret("CURSOR_USER_API_KEY")
# Backward-compatible alias for older env setups
CURSOR_API_KEY = _get_secret("CURSOR_API_KEY")

# Permanent admin by user ID
PERMANENT_ADMIN = 266952987128233985

# Paths relative to project root (where integrations.py lives) so they work regardless of cwd
_ROOT = os.path.dirname(os.path.abspath(__file__))
WHITELIST_FILE = os.path.join(_ROOT, "whitelist.json")
CONFIG_FILE = "config.json"

# System time and location
SYSTEM_TIME = None
SYSTEM_DATE = None
LOCATION = None
CITY = None
COUNTRY = None
PUBLIC_IP = None

def validate_tokens():
    """Validate bot/API tokens and report startup issues."""
    errors = []

    discord_missing = not DISCORD_BOT_TOKEN
    telegram_missing = not TELEGRAM_BOT_TOKEN

    if discord_missing and telegram_missing:
        errors.append("❌ Neither DISCORD_BOT_TOKEN nor TELEGRAM_BOT_TOKEN is set in environment variables or .env file")
    elif not discord_missing and (
        DISCORD_BOT_TOKEN == 'your_actual_discord_token_here' or 'YOUR_TOKEN' in DISCORD_BOT_TOKEN
    ):
        errors.append("❌ DISCORD_BOT_TOKEN is still set to the default/placeholder value")
    elif not telegram_missing and (
        TELEGRAM_BOT_TOKEN == 'your_actual_telegram_token_here' or 'YOUR_TOKEN' in TELEGRAM_BOT_TOKEN
    ):
        errors.append("❌ TELEGRAM_BOT_TOKEN is still set to the default/placeholder value")
    
    if not HA_ACCESS_TOKEN:
        errors.append("⚠️  HA_ACCESS_TOKEN is not set (Home Assistant commands will not work)")
    
    return errors

def update_system_time_date():
    """Update system time and date"""
    global SYSTEM_TIME, SYSTEM_DATE
    now = datetime.datetime.now()
    SYSTEM_DATE = now.strftime("%Y-%m-%d")
    SYSTEM_TIME = now.strftime("%H:%M:%S")
    return SYSTEM_DATE, SYSTEM_TIME

def get_location_by_ip():
    """Get location from IP address"""
    global LOCATION, CITY, COUNTRY, PUBLIC_IP
    
    try:
        # Get public IP
        ip_response = requests.get('https://api.ipify.org?format=json', timeout=5)
        ip_data = ip_response.json()
        PUBLIC_IP = ip_data.get('ip')
        
        if PUBLIC_IP:
            # Get location info
            location_response = requests.get(f'http://ip-api.com/json/{PUBLIC_IP}', timeout=5)
            location_data = location_response.json()
            
            if location_data.get('status') == 'success':
                CITY = location_data.get('city', 'Unknown')
                COUNTRY = location_data.get('country', 'Unknown')
                LOCATION = f"{CITY}, {COUNTRY}"
            else:
                LOCATION = "Unknown"
                CITY = "Unknown"
                COUNTRY = "Unknown"
        else:
            LOCATION = "Unknown"
            CITY = "Unknown"
            COUNTRY = "Unknown"
            
    except Exception as e:
        print(f"⚠️  Error getting location: {e}")
        LOCATION = "Unknown"
        CITY = "Unknown"
        COUNTRY = "Unknown"
    
    return LOCATION, CITY, COUNTRY


def refresh_environment_location() -> None:
    """Re-fetch public IP and geo from the host (blocking). Syncs LLM runtime location cache."""
    get_location_by_ip()
    try:
        from utils import llm_service as _lm

        _lm.sync_location_cache_from_integrations()
    except Exception:
        pass


async def refresh_environment_location_async() -> None:
    """Same as refresh_environment_location but runs the network calls in a thread pool."""
    import asyncio

    await asyncio.to_thread(refresh_environment_location)


def start_location_updater():
    """Run location lookup once on startup; retry until a location is found, then stop."""
    def try_until_found():
        max_attempts = 20
        interval = 30
        for attempt in range(max_attempts):
            try:
                get_location_by_ip()
                if LOCATION and LOCATION != "Unknown":
                    if attempt > 0:
                        print("✅ Location found")
                    return
            except Exception as e:
                print(f"⚠️  Error getting location (attempt {attempt + 1}/{max_attempts}): {e}")
            if attempt < max_attempts - 1:
                time.sleep(interval)
        print("⚠️  Could not get location after maximum attempts")

    thread = threading.Thread(target=try_until_found, daemon=True)
    thread.start()

# Validate tokens on startup
token_errors = validate_tokens()
if token_errors:
    print("\n" + "="*50)
    print("TOKEN CONFIGURATION ISSUES:")
    for error in token_errors:
        print(error)
    print("="*50 + "\n")
    
    if "Neither DISCORD_BOT_TOKEN nor TELEGRAM_BOT_TOKEN" in str(token_errors[0]):
        print("❌ Cannot start without at least one valid bot token")
        print("\nTo fix this:")
        print("1. For Discord: set DISCORD_BOT_TOKEN in .env")
        print("2. For Telegram: set TELEGRAM_BOT_TOKEN in .env")
        print("3. Run the bot again")
        sys.exit(1)

# Initialize the variables
update_system_time_date()
get_location_by_ip()
# If we didn't get a location yet, retry in background until found (then stop)
if not LOCATION or LOCATION == "Unknown":
    start_location_updater()
