"""Plain-text translation using the user's 'translate' function model and persona."""
from utils.llm.fallback import try_models_with_fallback
from utils.llm.prompts import function_model, function_system, get_enhanced_prompt


async def do_translate(user_id: int, text: str, target_language: str = "English") -> str:
    model_name, provider = function_model(user_id, "translate")
    system = function_system("translate", get_enhanced_prompt("translate", target_language=target_language))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    try:
        _, out = await try_models_with_fallback(model_name, messages, images=False, provider=provider)
        return (out or "").strip()
    except Exception:
        return ""
