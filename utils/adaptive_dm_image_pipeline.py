"""Adaptive DM image flow: draft reply → compact image prompt → image → reply refined against the image."""

from __future__ import annotations

import re
from typing import Optional, Tuple
from utils.llm_service import (
    adaptive_dm_image_flow_compress_image_prompt,
    adaptive_dm_image_flow_draft_reply,
    adaptive_dm_image_flow_refine_text_for_image,
    _strip_leaked_agent_tool_artifacts,
)
from utils.openrouter_image import OPENROUTER_IMAGE_GEN_SYSTEM_PROMPT, generate_openrouter_image_with_fallback


def _sanitize_final_message(text: str, image_prompt: str) -> str:
    """Drop accidental leakage of the internal image prompt into the user-visible reply."""
    if not (text or "").strip():
        return ""
    t = (text or "").strip()
    low = t.lower()
    ip = (image_prompt or "").strip()
    if ip and len(ip) > 12:
        idx = low.find(ip.lower())
        if idx != -1:
            t = t[:idx].rstrip()
    # Strip common "second message" image-description patterns (vision models sometimes add these)
    t = re.sub(
        r"\n*\[Sent a generated image:\s*[^\]]+\]\s*",
        "\n",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\n*\*{0,3}\s*Sent a generated image:\s*.*$",
        "",
        t,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Cut after horizontal rule often used before an appended description
    msep = re.search(r"\n\s*\*{3,}\s*\n", t)
    if msep:
        tail = t[msep.end() :].strip()
        if "photorealistic" in tail.lower() or "render" in tail.lower():
            t = t[: msep.start()].rstrip()
    msep2 = re.search(r"\n\s*-{3,}\s*\n", t)
    if msep2 and "photorealistic" in t[msep2.end() :].lower():
        t = t[: msep2.start()].rstrip()
    # If model echoed "User ask:" or similar, cut from there
    for marker in ("\nuser ask:", "\nassistant reply:", "\nimage prompt:", "\nprompt:"):
        p = t.lower().find(marker)
        if p != -1:
            t = t[:p].rstrip()
            break
    t = _strip_leaked_agent_tool_artifacts(t)
    return t.strip() or ""


def _strip_image_prompt_garbage(raw: str) -> str:
    """Keep a single image prompt line; drop accidental labels or draft echo."""
    if not raw:
        return ""
    t = raw.strip()
    # Drop common prefixes the model might add
    for prefix in (
        "image prompt:",
        "prompt:",
        "output:",
        "english image prompt:",
    ):
        if t.lower().startswith(prefix):
            t = t[len(prefix) :].strip()
    # First non-empty line only (avoid appended draft)
    for line in t.splitlines():
        s = line.strip()
        if s and not s.lower().startswith(("user ask", "assistant reply", "draft:")):
            t = s
            break
    # Remove quotes wrapping whole string
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        t = t[1:-1].strip()
    return t[:1200]


async def run_adaptive_dm_image_pipeline(
    user_id: int,
    channel_id: int,
    username: str,
    user_trigger: str,
    openrouter_model: str,
) -> Tuple[str, Optional[bytes], str, str]:
    """Returns (final_text, image_bytes, mime, error_message). On success error_message is empty."""
    try:
        draft = await adaptive_dm_image_flow_draft_reply(user_id, channel_id, username, user_trigger)

        raw_prompt = await adaptive_dm_image_flow_compress_image_prompt(user_id, draft, user_trigger)
        image_prompt = _strip_image_prompt_garbage(raw_prompt).strip()
        if not image_prompt:
            image_prompt = ((user_trigger or "")[:500] + "\n" + (draft or "")[:500]).strip()
        if not image_prompt:
            return "", None, "image/png", "Error: Empty image prompt."

        img_bytes, mime, _api_text, err = await generate_openrouter_image_with_fallback(
            openrouter_model,
            image_prompt,
            system_prompt=OPENROUTER_IMAGE_GEN_SYSTEM_PROMPT,
        )
        if err:
            return "", None, mime or "image/png", err
        if not img_bytes:
            return "", None, mime or "image/png", "Error: No image returned."

        refined = await adaptive_dm_image_flow_refine_text_for_image(
            user_id,
            draft,
            img_bytes,
            image_prompt=image_prompt,
        )
        refined = _sanitize_final_message(refined, image_prompt)
        if not refined or re.match(r"^\s*\[Sent a generated image", refined, flags=re.IGNORECASE):
            refined = (draft or "").strip()
        return refined, img_bytes, mime or "image/png", ""
    except Exception as e:
        return "", None, "image/png", f"Error: {str(e)[:200]}"
