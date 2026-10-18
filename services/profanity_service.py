"""Persistent profanity list and strict matching helpers."""
from __future__ import annotations

import re
from typing import List, Set

from utils.json_store import load_json, save_json
from utils.paths import data_path

DATA_PATH = data_path("profanity.json")

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


def _normalize_word(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (word or "").lower().translate(LEET_MAP))


def _load_raw_words() -> List[str]:
    data = load_json(DATA_PATH, None)
    words = data.get("words") if isinstance(data, dict) else None
    if not isinstance(words, list):
        return list(DEFAULT_WORDS)
    out = []
    for w in words:
        if isinstance(w, str):
            nw = _normalize_word(w)
            if nw:
                out.append(nw)
    return sorted(set(out))


def _save_words(words: List[str]) -> None:
    save_json(DATA_PATH, {"words": sorted(set(_normalize_word(w) for w in words if _normalize_word(w)))})


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
