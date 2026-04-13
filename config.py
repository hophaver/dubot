"""Bot config: wake word, startup channel, download limit, etc."""
import json
from typing import Any, Dict

CONFIG_FILE = "config.json"

DEFAULTS = {
    "wake_word": "robot",
    "is_awake": True,
    "startup_channel_id": None,
    "download_limit_mb": 100,
    "start_ollama_on_startup": False,
    "current_persona": "default",
    "chat_history": 20,
    # Auto-conversation settings
    "conversation_channels": [],
    "conversation_min_interval": 5,
    "conversation_max_interval": 20,
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


def is_bot_awake() -> bool:
    return bool(get_config().get("is_awake", DEFAULTS["is_awake"]))


def set_bot_awake(is_awake: bool) -> None:
    cfg = get_config()
    cfg["is_awake"] = bool(is_awake)
    save_config(cfg)


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


def get_conversation_channels():
    """Return list of channel IDs where auto-conversation is enabled."""
    cfg = get_config()
    raw = cfg.get("conversation_channels", []) or []
    ids = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def add_conversation_channel(channel_id: int) -> None:
    """Add a channel to auto-conversation list."""
    cfg = get_config()
    raw = cfg.get("conversation_channels", []) or []
    str_id = str(channel_id)
    if str_id not in [str(x) for x in raw]:
        raw.append(str_id)
        cfg["conversation_channels"] = raw
        save_config(cfg)


def remove_conversation_channel(channel_id: int) -> None:
    """Remove a channel from auto-conversation list."""
    cfg = get_config()
    raw = cfg.get("conversation_channels", []) or []
    str_id = str(channel_id)
    new_raw = [x for x in raw if str(x) != str_id]
    cfg["conversation_channels"] = new_raw
    save_config(cfg)


def get_conversation_frequency():
    """Return (min_messages, max_messages) for auto-conversation trigger."""
    cfg = get_config()
    try:
        min_n = int(cfg.get("conversation_min_interval", DEFAULTS["conversation_min_interval"]) or 5)
    except (TypeError, ValueError):
        min_n = 5
    try:
        max_n = int(cfg.get("conversation_max_interval", DEFAULTS["conversation_max_interval"]) or 20)
    except (TypeError, ValueError):
        max_n = 20
    min_n = max(1, min_n)
    if max_n < min_n:
        max_n = min_n
    return min_n, max_n


def set_conversation_frequency(min_messages: int, max_messages: int) -> None:
    """Set how often the bot auto-replies in conversation channels."""
    min_messages = max(1, int(min_messages))
    max_messages = max(min_messages, int(max_messages))
    cfg = get_config()
    cfg["conversation_min_interval"] = min_messages
    cfg["conversation_max_interval"] = max_messages
    save_config(cfg)
