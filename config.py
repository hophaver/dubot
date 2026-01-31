"""Bot config: wake word, startup channel, download limit, etc."""
import json
from typing import Any, Dict

CONFIG_FILE = "config.json"

DEFAULTS = {
    "wake_word": "robot",
    "startup_channel_id": None,
    "download_limit_mb": 100,
    "start_ollama_on_startup": False,
    "current_persona": "default",
    "chat_history": 20,
}


def get_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULTS, f, indent=4)
        return DEFAULTS.copy()


def save_config(config: Dict[str, Any]) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)


def get_wake_word() -> str:
    return get_config().get("wake_word", DEFAULTS["wake_word"])


def get_startup_channel_id():
    """Return the /sethome channel ID or None."""
    raw = get_config().get("startup_channel_id")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def get_download_limit_mb() -> int:
    return int(get_config().get("download_limit_mb", DEFAULTS["download_limit_mb"]))


def set_download_limit_mb(mb: int) -> None:
    cfg = get_config()
    cfg["download_limit_mb"] = max(1, min(2000, mb))
    save_config(cfg)


def get_current_persona() -> str:
    """Return the global persona name (used for all users)."""
    return get_config().get("current_persona", DEFAULTS["current_persona"]) or "default"


def set_current_persona(name: str) -> None:
    """Set the global persona for everyone."""
    cfg = get_config()
    cfg["current_persona"] = name
    save_config(cfg)


def get_chat_history() -> int:
    """Number of user messages to remember per chat (pairs = user+assistant, so 2x messages kept)."""
    return max(1, min(100, int(get_config().get("chat_history", DEFAULTS["chat_history"]) or 20)))


def set_chat_history(n: int) -> None:
    """Set how many user messages to remember per chat (1–100)."""
    cfg = get_config()
    cfg["chat_history"] = max(1, min(100, n))
    save_config(cfg)
