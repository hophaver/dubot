"""Ollama and OpenRouter chat clients: one async HTTP path, one retry policy.

Return convention (kept from the original helpers so callers don't change):
- reply text on success
- a string starting with "Error" on failure
- ``None`` when ``abort_check`` reported the request was superseded
"""
import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp

from integrations import OPENROUTER_CHAT_API_KEY
from utils import home_log, reliability_telemetry
from utils.http import OPENROUTER_CHAT_URL, aio_session, openrouter_headers, parse_openrouter_error
from utils.ollama import base_url as ollama_base_url

AbortCheck = Optional[Callable[[], Awaitable[bool]]]

OLLAMA_UNREACHABLE = "Error: Cannot connect to Ollama server."
OLLAMA_TIMEOUT_SECONDS = 75
OPENROUTER_TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 3
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_DEFAULT_TEXT_OPTIONS = {"temperature": 0.7, "num_predict": 1024}


def is_error_reply(text: Optional[str]) -> bool:
    """True for provider/fallback failure strings ("Error..." / "⚠️ ...")."""
    t = (text or "").lstrip()
    return t.startswith("Error") or t.startswith("⚠️")


async def _aborted(abort_check: AbortCheck) -> bool:
    return bool(abort_check and await abort_check())


def _ollama_endpoints() -> List[str]:
    primary = ollama_base_url()
    out = [primary]
    if "localhost" not in primary and "127.0.0.1" not in primary:
        out.append("http://localhost:11434")
    return out


def _ollama_messages(messages: list) -> list:
    out = []
    for msg in messages:
        m = {"role": msg.get("role", "user"), "content": msg.get("content", "")}
        if msg.get("images"):
            m["images"] = msg["images"]
        out.append(m)
    return out


async def ollama_chat(
    model_name: str,
    messages: list,
    request_options: Optional[Dict[str, Any]] = None,
    abort_check: AbortCheck = None,
) -> Optional[str]:
    """POST /api/chat. Retries transient failures; gives up immediately if the server is unreachable."""
    has_images = any(isinstance(m, dict) and m.get("images") for m in messages)
    # Image requests historically ran without a token cap; keep that unless the caller sets one.
    options: Dict[str, Any] = {} if has_images else dict(_DEFAULT_TEXT_OPTIONS)
    if isinstance(request_options, dict):
        options.update({k: v for k, v in request_options.items() if v is not None})
    data: Dict[str, Any] = {"model": model_name, "messages": _ollama_messages(messages), "stream": False}
    if options:
        data["options"] = options
    timeout = aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT_SECONDS)

    for endpoint in _ollama_endpoints():
        url = f"{endpoint}/api/chat"
        for attempt in range(MAX_ATTEMPTS):
            if await _aborted(abort_check):
                return None
            problem = ""
            try:
                async with aio_session().post(url, json=data, timeout=timeout) as resp:
                    status = resp.status
                    body_text = await resp.text()
            except aiohttp.ClientConnectorError:
                break  # nothing listening here; retrying won't help, try the next endpoint
            except asyncio.TimeoutError:
                problem = "timeout"
            except aiohttp.ClientError as e:
                problem = f"{type(e).__name__}: {str(e)[:160]}"
            else:
                if status == 404:
                    return f"Error 404: model `{model_name}` not found on Ollama."
                if status == 200:
                    try:
                        result = json.loads(body_text)
                        if await _aborted(abort_check):
                            return None
                        return result.get("message", {}).get("content", "No response.")
                    except ValueError:
                        problem = "invalid JSON response"
                elif status in _TRANSIENT_STATUS:
                    problem = f"HTTP {status}"
                else:
                    error_count = reliability_telemetry.increment("llm_errors")
                    home_log.log_sync(
                        f"🔴 LLM HTTP error {status} for model `{model_name}` (error #{error_count}): {body_text[:140]}"
                    )
                    return f"Error {status}: {body_text[:140]}"

            if attempt < MAX_ATTEMPTS - 1:
                reliability_telemetry.increment("llm_retries")
                home_log.log_sync(
                    f"⚠️ LLM {problem} for model `{model_name}` (attempt {attempt + 1}/{MAX_ATTEMPTS}). "
                    f"{reliability_telemetry.format_snapshot('Counters')}"
                )
                await asyncio.sleep(1 + attempt)
                continue
            counter = "llm_timeouts" if problem == "timeout" else "llm_errors"
            n = reliability_telemetry.increment(counter)
            home_log.log_sync(
                f"🔴 LLM {problem} after retries for model `{model_name}` (#{n}). "
                f"{reliability_telemetry.format_snapshot('Counters')}"
            )
            return "Error: Request timed out" if problem == "timeout" else f"Error: {problem}"

    error_count = reliability_telemetry.increment("llm_errors")
    home_log.log_sync(
        f"🔴 LLM connection failed for all Ollama endpoints (error #{error_count}) for model `{model_name}`. "
        f"{reliability_telemetry.format_snapshot('Counters')}"
    )
    return OLLAMA_UNREACHABLE


def to_openrouter_messages(messages: list) -> list:
    """Convert Ollama-style {"images": [b64...]} messages to OpenAI multimodal content parts."""
    converted = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        images = msg.get("images") or []
        if images:
            parts = []
            if content:
                parts.append({"type": "text", "text": str(content)})
            for base64_img in images:
                parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}})
            converted.append({"role": role, "content": parts})
        else:
            converted.append({"role": role, "content": str(content)})
    return converted


def message_text(message: Any) -> str:
    """Text of an OpenAI-style assistant message (string or list-of-parts content)."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, list):
        return "".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    return str(content or "").strip()


async def openrouter_post(
    payload: Dict[str, Any], *, timeout: float = OPENROUTER_TIMEOUT_SECONDS, api_key: Optional[str] = None
) -> Tuple[int, Any, str]:
    """POST chat/completions -> (status, parsed body or {}, raw text). Raises aiohttp/timeout errors."""
    key = api_key or OPENROUTER_CHAT_API_KEY
    async with aio_session().post(
        OPENROUTER_CHAT_URL,
        json=payload,
        headers=openrouter_headers(key),
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        raw = await resp.text()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return resp.status, body, raw


async def openrouter_chat(
    model_name: str,
    messages: list,
    request_options: Optional[Dict[str, Any]] = None,
    abort_check: AbortCheck = None,
) -> Optional[str]:
    if not OPENROUTER_CHAT_API_KEY:
        return "Error: OPENROUTER_API_KEY is not configured."
    opts = request_options or {}
    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": to_openrouter_messages(messages),
        "temperature": opts.get("temperature", 0.7),
    }
    if opts.get("num_predict") is not None:
        try:
            payload["max_tokens"] = int(max(64, min(4096, int(opts["num_predict"]))))
        except (TypeError, ValueError):
            pass

    try:
        for attempt in range(2):  # one retry on 429
            if await _aborted(abort_check):
                return None
            status, body, raw = await openrouter_post(payload)
            if await _aborted(abort_check):
                return None
            if status == 429 and attempt == 0:
                await asyncio.sleep(2)
                continue
            break
    except asyncio.TimeoutError:
        return "Error: Request timed out"
    except aiohttp.ClientError as e:
        return f"Error: {str(e)[:200]}"

    if status != 200:
        err = parse_openrouter_error(status, body, raw, model_name)
        if status == 401:
            home_log.log_sync(f"⚠️ OpenRouter 401 for model `{model_name}`: {err}")
        return f"Error: {err}"
    choices = (body.get("choices") or []) if isinstance(body, dict) else []
    if not choices or not isinstance(choices[0], dict):
        return "Error: No choices returned by OpenRouter."
    return message_text(choices[0].get("message")) or "No response."


async def chat(
    provider: str,
    model_name: str,
    messages: list,
    request_options: Optional[Dict[str, Any]] = None,
    abort_check: AbortCheck = None,
) -> Optional[str]:
    """Single request to one model on the given provider ("cloud" = OpenRouter, else Ollama)."""
    if (provider or "local").strip().lower() == "cloud":
        return await openrouter_chat(model_name, messages, request_options, abort_check)
    return await ollama_chat(model_name, messages, request_options, abort_check)
