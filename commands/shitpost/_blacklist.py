"""Blacklist of words the shitpost trigger ignores (data/shitpost_ignore.json)."""
from utils.json_store import load_json, save_json
from utils.paths import data_path

IGNORE_FILE = data_path("shitpost_ignore.json")


def _load() -> list[str]:
    data = load_json(IGNORE_FILE, [])
    return data if isinstance(data, list) else []


def _save(words: list[str]) -> None:
    save_json(IGNORE_FILE, words)


def get_ignored_words() -> list[str]:
    """Return blacklisted words (lowercase)."""
    return [w.lower() for w in _load()]


def add_ignored(word: str) -> bool:
    """Add word to blacklist (lowercased). Return True if added."""
    word = (word or "").strip().lower()
    if not word:
        return False
    words = _load()
    normalized = [w.lower() for w in words]
    if word in normalized:
        return False
    words.append(word)
    _save(words)
    return True

