"""Persistent profanity list and strict matching helpers."""
from __future__ import annotations

import json
import os
import re
from typing import List, Set

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(_ROOT, "data", "profanity.json")

DEFAULT_WORDS = [
    "asshole",
    "bastard",
    "bitch",
    "cock",
    "cunt",
    "dick",
    "fag",
    "faggot",
    "fuck",
    "fucker",
    "fucking",
    "motherfucker",
    "nigga",
    "nigger",
    "pussy",
    "retard",
    "shit",
    "whore",
]

LEET_MAP = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "6": "g",
        "7": "t",
        "8": "b",
        "9": "g",
        "@": "a",
        "$": "s",
        "!": "i",
    }
)


def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)


def _normalize_word(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (word or "").lower().translate(LEET_MAP))


def _load_raw_words() -> List[str]:
    _ensure_data_dir()
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        words = data.get("words", [])
        if not isinstance(words, list):
            return list(DEFAULT_WORDS)
        out = []
        for w in words:
            if isinstance(w, str):
                nw = _normalize_word(w)
                if nw:
                    out.append(nw)
        return sorted(set(out))
    except (FileNotFoundError, json.JSONDecodeError):
        return list(DEFAULT_WORDS)


def _save_words(words: List[str]) -> None:
    _ensure_data_dir()
    payload = {"words": sorted(set(_normalize_word(w) for w in words if _normalize_word(w)))}
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def get_words() -> List[str]:
    words = _load_raw_words()
    if not words:
        words = list(DEFAULT_WORDS)
    return sorted(set(words))


def add_word(word: str) -> bool:
    nw = _normalize_word(word)
    if not nw:
        return False
    words = set(get_words())
    before = len(words)
    words.add(nw)
    _save_words(sorted(words))
    return len(words) != before


def remove_word(word: str) -> bool:
    nw = _normalize_word(word)
    if not nw:
        return False
    words = set(get_words())
    if nw not in words:
        return False
    words.remove(nw)
    _save_words(sorted(words))
    return True


def reset_defaults() -> None:
    _save_words(list(DEFAULT_WORDS))


def contains_profanity(text: str) -> bool:
    if not text:
        return False
    words: Set[str] = set(get_words())
    if not words:
        return False

    lowered = text.lower().translate(LEET_MAP)
    tokenized = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = [t for t in tokenized.split() if t]

    # Word-level and repeated-character checks.
    for t in tokens:
        if t in words:
            return True
        squeezed = re.sub(r"(.)\1{2,}", r"\1", t)
        if squeezed in words:
            return True

    # Strict compact scan catches punctuation-separated forms like f.u.c.k.
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    for w in words:
        if w in compact:
            return True
    return False
