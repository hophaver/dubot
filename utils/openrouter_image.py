"""OpenRouter chat/completions image generation (modalities)."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from integrations import OPENROUTER_API_KEY
from utils import home_log

# System instructions for the image model (natural-language + contextual prompts).
OPENROUTER_IMAGE_GEN_SYSTEM_PROMPT = (
    "You are an image generation assistant on Discord. "
    "Follow the user's latest request and the conversation context you are given. "
    "Produce a single clear image that fits the ongoing discussion—visualize what they are trying to see "
    "(product, diagram, scene, UI mockup, etc.) when that is what the context calls for. "
    "Stay safe and on-topic; no gratuitous text in the image unless the user asked for labeled text. "
    "Photorealistic or illustrative style should match what the context implies."
)


def _parse_data_url(data_url: str) -> Optional[Tuple[bytes, str]]:
    """Return (raw_bytes, mime) from data:image/...;base64,..."""
    if not data_url or not isinstance(data_url, str):
        return None
    m = re.match(r"^data:([^;]+);base64,(.+)$", data_url.strip(), flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    mime = (m.group(1) or "image/png").strip().lower()
    b64 = m.group(2).strip()
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:
        return None
    if not raw:
        return None
    return raw, mime


def _extract_images_from_message(message: Any) -> List[Tuple[bytes, str]]:
    out: List[Tuple[bytes, str]] = []
    if not isinstance(message, dict):
        return out
    for key in ("images", "image_urls"):
        items = message.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            url = None
            if item.get("type") == "image_url" and isinstance(item.get("image_url"), dict):
                url = item["image_url"].get("url")
            elif isinstance(item.get("image_url"), dict):
                url = item["image_url"].get("url")
            elif isinstance(item.get("imageUrl"), dict):
                url = item["imageUrl"].get("url")
            if not url:
                continue
            parsed = _parse_data_url(str(url))
            if parsed:
                out.append(parsed)
    return out


def _should_try_next_modalities_combo(error_msg: str) -> bool:
    """True when OpenRouter rejected the modality mix — try another combo (e.g. image-only vs image+text)."""
    if not error_msg:
        return False
    low = error_msg.lower()
    return (
        "modalities" in low
        or "output modalities" in low
        or "no endpoints found" in low
    )


async def probe_openrouter_image_model(model_name: str) -> Tuple[bool, str]:
    """Minimal image-gen probe; does not persist."""
    _b, _mime, _text, err = await generate_openrouter_image_with_fallback(
        model_name,
        "Reply with only a single tiny solid-color square icon, minimal detail.",
        timeout=90,
    )
    if err:
        return False, err
    if not _b:
        return False, "Model responded but no image bytes were returned (check output_modalities includes image)."
    return True, f"Image model `{model_name}` OK on OpenRouter."


async def generate_openrouter_image_with_fallback(
    model_name: str,
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    timeout: int = 120,
    drop_assistant_text: bool = False,
) -> Tuple[Optional[bytes], str, str, str]:
    """
    Try modality combos in an order that works for both image-only models (e.g. Seedream) and
    image+text models (e.g. some Gemini image endpoints): image-only first, then image+text.
    Retries when OpenRouter returns modality/endpoint mismatch (e.g. 404 for image,text).
    """
    combos: Tuple[Tuple[str, ...], ...] = (("image",), ("image", "text"))
    last_err = ""
    last_text = ""
    last_mime = "image/png"
    for i, mods in enumerate(combos):
        b, mime, text, err = await generate_openrouter_image(
            model_name,
            user_prompt,
            modalities=mods,
            system_prompt=system_prompt,
            timeout=timeout,
        )
        if b:
            if drop_assistant_text:
                text = ""
            return b, mime, text, err
        last_err = err or ""
        last_text = text or ""
        last_mime = mime or "image/png"
        is_last = i >= len(combos) - 1
        if not is_last and _should_try_next_modalities_combo(last_err):
            continue
        if not is_last and "No image in API response" in last_err:
            continue
        break
    return None, last_mime, last_text, last_err or "Error: No image in API response (model may not support image output)."


async def generate_openrouter_image(
    model_name: str,
    user_prompt: str,
    *,
    modalities: Tuple[str, ...] = ("image", "text"),
    system_prompt: Optional[str] = None,
    timeout: int = 120,
) -> Tuple[Optional[bytes], str, str, str]:
    """
    Returns (image_bytes, mime, assistant_text, error_message).
    error_message empty on success. image_bytes None when only text or failure.
    """
    if not OPENROUTER_API_KEY:
        return None, "image/png", "", "Error: OPENROUTER_API_KEY is not configured."
    model_name = str(model_name or "").strip()
    if not model_name:
        return None, "image/png", "", "Error: No image model configured. Set one in /llm-settings (Image generation) or use /pull-model."

    url = "https://openrouter.ai/api/v1/chat/completions"
    messages: List[Dict[str, Any]] = []
    if system_prompt and str(system_prompt).strip():
        messages.append({"role": "system", "content": str(system_prompt).strip()})
    messages.append({"role": "user", "content": str(user_prompt or "").strip()})

    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "modalities": list(modalities),
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    def _post() -> requests.Response:
        return requests.post(url, json=payload, headers=headers, timeout=timeout)

    try:
        response = await asyncio.to_thread(_post)
    except requests.exceptions.Timeout:
        return None, "image/png", "", "Error: Image request timed out."
    except Exception as e:
        return None, "image/png", "", f"Error: {str(e)[:200]}"

    body: Dict[str, Any] = {}
    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code != 200:
        err_obj = body.get("error") if isinstance(body, dict) else {}
        detail = ""
        if isinstance(err_obj, dict):
            detail = str(err_obj.get("message", "") or "").strip()
        if not detail:
            detail = (response.text or "")[:220]
        return None, "image/png", "", f"Error: OpenRouter ({response.status_code}): {detail[:220]}"

    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None, "image/png", "", "Error: No choices returned by OpenRouter."

    msg = choices[0].get("message", {})
    if not isinstance(msg, dict):
        return None, "image/png", "", "Error: Invalid assistant message."

    text_out = ""
    content = msg.get("content", "")
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        text_out = "".join(parts).strip()
    elif content is not None:
        text_out = str(content).strip()

    blobs = _extract_images_from_message(msg)
    if not blobs:
        home_log.log_sync(f"⚠️ OpenRouter image gen: no images in message keys={list(msg.keys())}")
        return None, "image/png", text_out, "Error: No image in API response (model may not support image output or modalities mismatch)."

    raw, mime = blobs[0]
    return raw, mime or "image/png", text_out, ""
