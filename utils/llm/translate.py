"""Plain-text translation using the user's 'translate' function model and persona."""
from llm_function_prefs import get_function_persona_name
from models import model_manager
from personas import persona_manager


async def do_translate(user_id: int, text: str, target_language: str = "English") -> str:
    from utils.llm_service import _try_models_with_fallback, get_enhanced_prompt

    eff = model_manager.get_effective_model_for_function(user_id, "translate")
    model_name = eff.get("model") or model_manager.get_last_local_model(user_id, refresh_local=True)
    provider = eff.get("provider", "local")
    base = persona_manager.get_persona(get_function_persona_name("translate")).strip()
    instr = get_enhanced_prompt("translate", target_language=target_language)
    system = f"{base}\n\n{instr}" if base else instr
    messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    try:
        _, out = await _try_models_with_fallback(model_name, messages, images=False, provider=provider)
        return (out or "").strip()
    except Exception:
        return ""
