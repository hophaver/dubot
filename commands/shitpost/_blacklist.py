"""Blacklist of words the shitpost trigger ignores (data/shitpost_ignore.json)."""
import json
import os

IGNORE_FILE = os.path.join("data", "shitpost_ignore.json")


def _load() -> list[str]:
    try:
        with open(IGNORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(words: list[str]) -> None:
    os.makedirs(os.path.dirname(IGNORE_FILE), exist_ok=True)
    with open(IGNORE_FILE, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2)


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


def remove_ignored(word: str) -> bool:
    """Remove word from blacklist. Return True if removed."""
    word = (word or "").strip().lower()
    words = _load()
    normalized = [w.lower() for w in words]
    if word not in normalized:
        return False
    words = [w for w in words if w.lower() != word]
    _save(words)
    return True
