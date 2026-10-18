"""Model selection with fallback (local chain, vision candidates) and model validation."""
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from models import model_manager
from utils import home_log
from utils.llm import providers
from utils.openrouter_image import probe_openrouter_image_model

def _get_fallback_chain():
    from utils.model_fallback import get_fallback_chain
    return get_fallback_chain()


# requested model -> local model that last answered an image request for it
_vision_model_cache: Dict[str, str] = {}


def clear_vision_model_cache() -> None:
    """Call when model list changes (e.g. after /pull-model or remove) so vision is re-resolved."""
    _vision_model_cache.clear()


# Name patterns that typically indicate vision-capable models (Ollama)
VISION_NAME_PATTERNS = ("llava", "llama3.2", "llama3.1", "pixtral", "minicpm-v", "vision", "moondream", "bakllava", "llava-phi", "nano-llava")


def _is_vision_capable(model_name: str) -> bool:
    """Heuristic: model name suggests vision support."""
    if not model_name:
        return False
    lower = model_name.lower()
    return any(p in lower for p in VISION_NAME_PATTERNS)


def _format_vision_help_message() -> str:
    """Build message listing available models with vision-capable marked. Call when image request fails."""
    models = model_manager.list_all_models(refresh_local=True)
    if not models:
        return "No vision-capable model could process this image. No models are available. Use **/pull-model local model_name** to install one (e.g. `llava` or `llama3.2:3b`)."
    lines = ["No vision-capable model could process this image. Use **/llm-settings** or **/pull-model local model_name** to install one.", "", "**Available models** (✅ = typically vision-capable):", ""]
    for m in models:
        mark = "✅" if _is_vision_capable(m) else "○"
        lines.append(f"{mark} `{m}`")
    return "\n".join(lines)


_MAX_VISION_CANDIDATES = 6


def _vision_candidates(requested_model: str) -> List[str]:
    """Order to try for an image request: last known-good, requested, likely-vision installed, the rest."""
    available = model_manager.list_all_models(refresh_local=False) or _get_fallback_chain()
    cached = _vision_model_cache.get(requested_model)
    likely = [m for m in available if _is_vision_capable(m)]
    others = [m for m in available if not _is_vision_capable(m)]
    ordered = dict.fromkeys(m for m in [cached, requested_model, *likely, *others] if m)
    return list(ordered)[:_MAX_VISION_CANDIDATES]


async def try_models_with_fallback(
    requested_model,
    messages,
    images=False,
    provider="local",
    request_options: Optional[Dict[str, Any]] = None,
    abort_check: Optional[Callable[[], Awaitable[bool]]] = None,
):
    provider = (provider or "local").strip().lower()
    if provider == "cloud":
        response = await providers.openrouter_chat(requested_model, messages, request_options, abort_check)
        if response is None:
            return requested_model, ""
        if response and not response.startswith("Error:"):
            return requested_model, response
        return requested_model, f"⚠️ Cloud model unavailable: {response}"

    if images:
        # Each candidate gets the real request once; the first that answers is remembered.
        models_to_try = _vision_candidates(requested_model)
    else:
        models_to_try = [requested_model] + _get_fallback_chain()

    models_to_try = list(dict.fromkeys(models_to_try))
    for model_name in models_to_try:
        if abort_check and await abort_check():
            return requested_model, ""
        response = await providers.ollama_chat(model_name, messages, request_options, abort_check)
        if response is None:
            return requested_model, ""
        if response == providers.OLLAMA_UNREACHABLE:
            break  # server is down; every other local model would fail the same way

        if response and not response.startswith("Error:"):
            if images:
                _vision_model_cache[requested_model] = model_name
            return model_name, response

        if "404" in response or "not found" in response.lower():
            continue

        home_log.log_sync(f"Error with model {model_name}: {response}")

    if images:
        return requested_model, _format_vision_help_message()
    return requested_model, "⚠️ All models are unavailable. Please check your Ollama server."


async def probe_model(provider: str, model_name: str) -> Tuple[bool, str]:
    """Return (ok, message) after a minimal completion probe (does not persist)."""
    provider = (provider or "local").strip().lower()
    if provider not in {"local", "cloud"}:
        return False, "Provider must be 'local' or 'cloud'."
    test_messages = [{"role": "user", "content": "Test"}]
    response = await providers.chat(provider, model_name, test_messages)
    if response is None:
        return False, f"Cannot use model '{model_name}' (aborted)."
    if response and not response.startswith("Error:"):
        return True, f"Model '{model_name}' OK ({provider})."
    return False, f"Cannot use model '{model_name}'. {response}"


async def validate_and_set_model(user_id, provider, model_name):
    ok, msg = await probe_model(provider, model_name)
    if ok:
        model_manager.set_user_model(user_id, model_name, provider=provider)
        return True, f"Model '{model_name}' set ({provider})."
    return False, msg


async def validate_and_set_function_model(user_id, function_key: str, provider: str, model_name: str) -> Tuple[bool, str]:
    if str(function_key) == "image_generation":
        if (provider or "").strip().lower() != "cloud":
            return False, "Image generation uses OpenRouter only — pick a **cloud** model id."
        return await validate_and_set_image_generation_model(user_id, model_name)
    ok, msg = await probe_model(provider, model_name)
    if ok:
        model_manager.set_function_model(user_id, function_key, model_name, provider=provider)
        return True, f"Saved for this function: `{model_name}` ({provider})."
    return False, msg


async def validate_and_set_image_generation_model(user_id: int, model_name: str) -> Tuple[bool, str]:
    """Validate an OpenRouter image-generation model and save under function image_generation."""
    name = str(model_name or "").strip()
    if not name:
        return False, "Model name is empty."
    ok, msg = await probe_openrouter_image_model(name)
    if ok:
        model_manager.set_function_model(user_id, "image_generation", name, provider="cloud")
        return True, f"Image model `{name}` validated and saved. Use **`/llm-settings`** if you need to change it later."
    return False, msg
