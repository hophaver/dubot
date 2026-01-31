"""Model fallback chain: load from model_fallback.json, sorted by size (largest first)."""
import json
import os
import re

FALLBACK_FILE = "model_fallback.json"
_sorted_chain: list = []


def _size_key(name: str) -> int:
    m = re.search(r"(\d+)\s*b\s*$", name.lower()) or re.search(r":(\d+)b", name.lower())
    return int(m.group(1)) if m else 0


def load_and_sort_fallback() -> list:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, FALLBACK_FILE)
    try:
        with open(path) as f:
            data = json.load(f)
        models = data.get("models", [])
    except (FileNotFoundError, json.JSONDecodeError):
        models = ["llama3.2:3b", "qwen2.5:7b", "llama3.2:1b"]
    global _sorted_chain
    _sorted_chain = sorted(models, key=_size_key, reverse=True)
    return _sorted_chain


def get_fallback_chain() -> list:
    if not _sorted_chain:
        load_and_sort_fallback()
    return list(_sorted_chain)
