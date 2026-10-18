"""Shared HTTP clients and OpenRouter helpers (connection reuse instead of a fresh socket per call)."""
from typing import Any, Dict, Optional

import aiohttp
import requests

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE}/chat/completions"

_session: Optional[requests.Session] = None
_aio: Optional[aiohttp.ClientSession] = None


def session() -> requests.Session:
    """Process-wide requests.Session (for blocking calls; run them via asyncio.to_thread)."""
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def aio_session() -> aiohttp.ClientSession:
    """Lazy shared aiohttp session; must be called from within the running event loop."""
    global _aio
    if _aio is None or _aio.closed:
        _aio = aiohttp.ClientSession()
    return _aio


async def close() -> None:
    global _aio, _session
    if _aio is not None and not _aio.closed:
        await _aio.close()
    _aio = None
    if _session is not None:
        _session.close()
    _session = None


def openrouter_headers(api_key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def parse_openrouter_error(status_code: int, body: Any, raw_text: str = "", model_name: str = "") -> str:
    """Human-readable message for a non-200 OpenRouter response."""
    error_obj = body.get("error") if isinstance(body, dict) else {}
    if not isinstance(error_obj, dict):
        error_obj = {}
    message = str(error_obj.get("message", "")).strip()
    metadata = error_obj.get("metadata") if isinstance(error_obj.get("metadata"), dict) else {}
    raw_meta = str(metadata.get("raw", "")).strip()
    detail = raw_meta or message or (raw_text or "").strip()
    model_label = f"`{model_name}`" if model_name else "this model"
    if status_code == 429:
        return (
            f"Rate limited for {model_label} on OpenRouter. "
            "Retry in a moment, switch to another cloud model, or use a local model."
        )
    if status_code == 401:
        return f"OpenRouter authentication failed. Check OPENROUTER_API_KEY.{(' Details: ' + detail[:180]) if detail else ''}"
    if status_code == 402:
        return "OpenRouter credits required for this request. Top up credits or choose another model."
    if detail:
        return f"OpenRouter request failed ({status_code}): {detail[:220]}"
    return f"OpenRouter request failed ({status_code})."
