"""Per-function LLM preferences (model + persona for non-adaptive paths).

Personas for functions are stored in config.json under function_personas.
Model overrides per function live in data/models.json under user_models[].function_models.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from config import get_config, save_config, get_current_persona

# Stable keys used in storage and UI
LLM_FUNCTION_KEYS: List[str] = [
    "chat",
    "dm_summary",
    "command_planner",
    "file_analysis",
    "compare_files",
    "translate",
    "shitpost",
    "image_generation",
]

LLM_FUNCTION_LABELS: Dict[str, str] = {
    "chat": "Chat (servers & non-adaptive DMs)",
    "dm_summary": "DM rolling history summary",
    "command_planner": "DM adaptive command planner",
    "file_analysis": "File analysis (analyze, OCR, review, …)",
    "compare_files": "Compare files",
    "translate": "Translate",
    "shitpost": "Shitpost (!word / .word)",
    "image_generation": "Image generation (/imagine; OpenRouter only)",
}


def function_label(key: str) -> str:
    return LLM_FUNCTION_LABELS.get(key, key)


_UTILITY_DEFAULT_PERSONAS = {
    "dm_summary": "__utility_dm_summary__",
    "command_planner": "__utility_command_planner__",
    "file_analysis": "__utility_file_analysis__",
    "compare_files": "__utility_compare_files__",
    "translate": "__utility_translate__",
}


def get_function_persona_name(function_key: str) -> str:
    cfg = get_config()
    raw = cfg.get("function_personas") if isinstance(cfg.get("function_personas"), dict) else {}
    name = str(raw.get(function_key, "") or "").strip()
    if name and name != "__default__":
        return name
    if function_key in _UTILITY_DEFAULT_PERSONAS:
        return _UTILITY_DEFAULT_PERSONAS[function_key]
    return get_current_persona()


def set_function_persona_name(function_key: str, persona_name: str) -> None:
    cfg = get_config()
    fp = cfg.get("function_personas")
    if not isinstance(fp, dict):
        fp = {}
    fp[function_key] = str(persona_name or "").strip() or "__default__"
    cfg["function_personas"] = fp
    save_config(cfg)


def list_function_persona_status() -> Dict[str, str]:
    """Resolved persona name per function key (after defaulting)."""
    return {k: get_function_persona_name(k) for k in LLM_FUNCTION_KEYS}
