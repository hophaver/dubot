"""System prompt templates (system_prompts.json) and per-function model/persona resolution."""
import json
from typing import Optional, Tuple

from llm_function_prefs import get_function_persona_name
from models import model_manager
from personas import persona_manager
from utils.paths import root_path

_system_prompts_cache = None


def _get_system_prompts():
    global _system_prompts_cache
    if _system_prompts_cache is None:
        path = root_path("system_prompts.json")
        try:
            with open(path) as f:
                _system_prompts_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _system_prompts_cache = {}
    return _system_prompts_cache


def get_enhanced_prompt(key: str, **kwargs) -> str:
    prompts = _get_system_prompts()
    tpl = prompts.get(key, "")
    for k, v in kwargs.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl


def function_model(user_id, function_key: str) -> Tuple[str, str]:
    """(model, provider) for a logical LLM function: per-function override, else the user's default."""
    eff = model_manager.get_effective_model_for_function(user_id, function_key)
    return eff["model"], eff.get("provider", "local")


def default_model(user_id) -> Tuple[str, str]:
    """(model, provider) of the user's default chat model (ignores per-function overrides)."""
    info = model_manager.get_user_model_info(user_id)
    return info["model"], info.get("provider", "local")


def function_system(function_key: str, *parts: Optional[str]) -> str:
    """System prompt = the function's persona followed by the given instruction blocks."""
    base = persona_manager.get_persona(get_function_persona_name(function_key)).strip()
    return "\n\n".join(p for p in (base, *parts) if p)
