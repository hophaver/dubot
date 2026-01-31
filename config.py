"""Bot config: wake word, startup channel, download limit, etc. Stored in config.json."""
import json
from typing import Any, Dict

CONFIG_FILE = "config.json"

DEFAULTS = {
    "wake_word": "robot",
    "startup_channel_id": None,
    "download_limit_mb": 100,
    "start_ollama_on_startup": False,
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
