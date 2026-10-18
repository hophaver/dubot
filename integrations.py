"""Environment-backed settings (.env), permanent admin, and host time/location."""
import asyncio
import datetime
import os
import re
import sys
import time
from typing import Tuple

from utils.paths import migrate_root_file, root_path

try:
    from dotenv import dotenv_values

    # Non-empty .env values win over the inherited shell environment (stale exports shouldn't shadow
    # the file); empty ones leave any shell value in place.
    for _k, _v in dotenv_values(root_path(".env")).items():
        if _v:
            os.environ[_k] = _v
except ImportError:
    print("⚠️  python-dotenv not installed, using system environment variables")

_PLACEHOLDERS = {
    "TOKEN",
    "YOUR_TOKEN",
    "YOUR_API_KEY",
    "API_KEY",
    "CHANGE_ME",
    "REPLACE_ME",
    "URL",
    "YOUR_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_CHAT_API_KEY",
    "OPENROUTER_MANAGEMENT_API_KEY",
}


def _normalize_secret(value: str) -> str:
    v = str(value or "").strip().strip('"').strip("'")
    # Remove common invisible/control characters from copy-pasted secrets.
    v = v.replace("﻿", "").replace("​", "").replace("‌", "").replace("‍", "")
    v = "".join(ch for ch in v if ch.isprintable())
    # Remove all whitespace characters that can sneak in from copy/paste.
    v = re.sub(r"\s+", "", v)
    if v.lower() in {"none", "null"}:
        return ""
    # Treat common template placeholders as unset so fallback keys can be used.
    if v.upper() in _PLACEHOLDERS:
        return ""
    return v


def _get_secret(*keys: str) -> str:
    """First non-empty value among ``keys`` (whitespace/placeholder-normalized)."""
    for key in keys:
        v = _normalize_secret(os.environ.get(key, ""))
        if v:
            return v
    return ""


def _env_raw(key: str) -> str:
    """Single env value for non-secret strings (e.g. model names); strip quotes only, keep spaces/slashes."""
    s = str(os.environ.get(key, "") or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return "" if s.lower() in ("none", "null") else s


def _env_bool(key: str, default: bool = True) -> bool:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on", "y")


DISCORD_BOT_TOKEN = _get_secret("DISCORD_BOT_TOKEN")
HA_URL = _get_secret("HA_URL").rstrip("/")
HA_ACCESS_TOKEN = _get_secret("HA_ACCESS_TOKEN")
OLLAMA_URL = _get_secret("OLLAMA_URL") or "http://localhost:11434"

# /himas: try Home Assistant Assist (conversation API) first; LLM fallback uses HIMAS_PARSE_* below.
HIMAS_ASSIST_ENABLED = _env_bool("HIMAS_ASSIST_ENABLED", True)
HIMAS_ASSIST_LANGUAGE = _get_secret("HIMAS_ASSIST_LANGUAGE") or "en"
HIMAS_ASSIST_AGENT_ID = _env_raw("HIMAS_ASSIST_AGENT_ID")
# auto = user's preferred model (cloud vs local); ollama | openrouter = fixed backend.
HIMAS_PARSE_PROVIDER = (_env_raw("HIMAS_PARSE_PROVIDER") or "auto").lower()
HIMAS_PARSE_MODEL = _env_raw("HIMAS_PARSE_MODEL")

# OpenRouter keys:
# - OPENROUTER_API_KEY is the primary key (aliases: OPENROUTER_KEY, OPENROUTER_APIKEY).
# - OPENROUTER_CHAT_API_KEY optionally overrides it for chat/completions and image generation.
# - OPENROUTER_MANAGEMENT_API_KEY optionally overrides it for /bal credits.
OPENROUTER_API_KEY = _get_secret("OPENROUTER_API_KEY", "OPENROUTER_KEY", "OPENROUTER_APIKEY")
OPENROUTER_CHAT_API_KEY = _get_secret("OPENROUTER_CHAT_API_KEY") or OPENROUTER_API_KEY
OPENROUTER_MANAGEMENT_API_KEY = _get_secret("OPENROUTER_MANAGEMENT_API_KEY") or OPENROUTER_API_KEY
# Cursor user key (preferred var for /cursor spend check attempts); CURSOR_API_KEY is a legacy alias.
CURSOR_USER_API_KEY = _get_secret("CURSOR_USER_API_KEY")
CURSOR_API_KEY = _get_secret("CURSOR_API_KEY")

# Permanent admin by user ID
PERMANENT_ADMIN = 266952987128233985

WHITELIST_FILE = migrate_root_file("whitelist.json")

# System time and location (location is filled lazily; see get_location_cached)
SYSTEM_TIME = None
SYSTEM_DATE = None
LOCATION = None
CITY = None
COUNTRY = None
PUBLIC_IP = None
_LOCATION_RETRY_SECONDS = 600
_location_checked_at = 0.0


def validate_tokens():
    """Validate bot/API tokens and report startup issues."""
    errors = []
    if not DISCORD_BOT_TOKEN:
        errors.append("❌ DISCORD_BOT_TOKEN is not set in environment variables or .env file")
    elif DISCORD_BOT_TOKEN == "your_actual_discord_token_here" or "YOUR_TOKEN" in DISCORD_BOT_TOKEN:
        errors.append("❌ DISCORD_BOT_TOKEN is still set to the default/placeholder value")
    if not HA_ACCESS_TOKEN or not HA_URL:
        errors.append("⚠️  HA_URL / HA_ACCESS_TOKEN not set (Home Assistant commands will not work)")
    return errors


def update_system_time_date():
    """Update system time and date. Time is minute-granular so prompts that embed it stay cacheable."""
    global SYSTEM_TIME, SYSTEM_DATE
    now = datetime.datetime.now()
    SYSTEM_DATE = now.strftime("%Y-%m-%d")
    SYSTEM_TIME = now.strftime("%H:%M")
    return SYSTEM_DATE, SYSTEM_TIME


def get_location_by_ip():
    """Look up public IP and geo location (blocking network calls)."""
    global LOCATION, CITY, COUNTRY, PUBLIC_IP, _location_checked_at
    from utils.http import session

    _location_checked_at = time.time()
    LOCATION = CITY = COUNTRY = "Unknown"
    try:
        PUBLIC_IP = session().get("https://api.ipify.org?format=json", timeout=5).json().get("ip")
        if PUBLIC_IP:
            data = session().get(f"http://ip-api.com/json/{PUBLIC_IP}", timeout=5).json()
            if data.get("status") == "success":
                CITY = data.get("city", "Unknown")
                COUNTRY = data.get("country", "Unknown")
                LOCATION = f"{CITY}, {COUNTRY}"
    except Exception as e:
        print(f"⚠️  Error getting location: {e}")
    return LOCATION, CITY, COUNTRY


async def refresh_environment_location_async() -> None:
    """Re-fetch public IP and geo from the host without blocking the event loop."""
    await asyncio.to_thread(get_location_by_ip)


async def get_location_cached() -> Tuple[str, str, str]:
    """(location, city, country); retries an unknown location at most every 10 minutes."""
    known = LOCATION and LOCATION != "Unknown"
    if not known and time.time() - _location_checked_at >= _LOCATION_RETRY_SECONDS:
        await refresh_environment_location_async()
    return LOCATION or "Unknown", CITY or "Unknown", COUNTRY or "Unknown"


# Validate tokens on startup
token_errors = validate_tokens()
if token_errors:
    print("\n" + "=" * 50)
    print("TOKEN CONFIGURATION ISSUES:")
    for error in token_errors:
        print(error)
    print("=" * 50 + "\n")
    if token_errors[0].startswith("❌"):
        print("❌ Cannot start without a valid Discord bot token")
        print("\nTo fix this:")
        print("1. Set DISCORD_BOT_TOKEN in .env")
        print("2. Run the bot again")
        sys.exit(1)

update_system_time_date()
