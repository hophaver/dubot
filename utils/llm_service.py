"""Chat entry points (ask_llm and friends). Building blocks live in utils/llm/; re-exported here for callers."""
import base64
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

import integrations
from adaptive_dm import (
    ADAPTIVE_DM_BASE_PERSONA,
    ADAPTIVE_DM_SYSTEM_SUFFIX,
    adaptive_dm_manager,
    is_adaptive_context_export_filename,
)
from conversations import conversation_manager, is_news_style_dm_bot_text
from models import model_manager
from personas import persona_manager
from llm_function_prefs import get_function_persona_name
from utils.llm.command_db import command_db
from utils.llm.dm_memory import (
    compact_dm_history_for_channel,
    merge_adaptive_manual_guidance_into_profile,
    schedule_dm_adaptive_background_tasks,
)
from utils.llm.fallback import (
    clear_vision_model_cache,
    probe_model,
    try_models_with_fallback,
    validate_and_set_function_model,
    validate_and_set_image_generation_model,
    validate_and_set_model,
)
from utils.llm.files import SUPPORTED_FILE_TYPES, FileProcessor, analyze_file, compare_files
from utils.llm.prompts import default_model, function_model, function_system, get_enhanced_prompt
from utils.llm.providers import is_error_reply
from utils.llm.text import (
    clean_response,
    extract_json_object,
    extract_json_object_loose,
    strip_leaked_agent_tool_artifacts,
    strip_leaked_image_placeholders,
)

_COMMAND_HINT_KEYWORDS = (
    "command", "help", "what can you do", "how do i", "how to",
    "show me", "list", "available", "options", "features",
)
_ADAPTIVE_IMAGE_NOTE = (
    "You can generate images for this user when they clearly ask for a visual (they have an image model configured). "
    "Do not claim you are text-only or cannot draw/render if they ask for a picture—offer `/imagine` or acknowledge images can be produced in this DM.\n"
    "If the transcript notes you sent an image earlier, treat that as context only; reference it when relevant. "
    "Never write bracketed placeholders or pretend to attach images in text—the user only sees real attachments."
)
_DM_IMAGE_NOTE = (
    "This user has an **image generation** model configured. "
    "Do not claim you are text-only or cannot produce images if they ask for a picture—point them to **`/imagine`**.\n"
    "If the transcript notes you sent an image earlier, refer to it only when relevant. "
    "Never output bracketed placeholders for images."
)


def _has_image_model(user_id) -> bool:
    return bool(str(model_manager.get_effective_model_for_function(user_id, "image_generation").get("model") or "").strip())


def _command_suggestions(message_text: str) -> str:
    lower = message_text.lower()
    if not (any(k in lower for k in _COMMAND_HINT_KEYWORDS) or "?" in lower):
        return ""
    suggestions = command_db.get_suggestions(message_text, limit=3)
    if not suggestions:
        return ""
    lines = ["\n\nIf you're looking for commands, here are some suggestions:"]
    lines += [f"- `/{s['name']}`: {s['description']}" for s in suggestions]
    lines.append("\nUse `/help` to see all available commands or `/help [search]` to search.")
    return "\n".join(lines) + "\n"


def _attachment_context(attachments) -> tuple:
    """(text listing the uploads, [base64 images])."""
    if not attachments:
        return "", []
    lines = ["\n\nUser has uploaded the following files:"]
    images: List[str] = []
    for i, attachment in enumerate(attachments, 1):
        fn = attachment.get("filename") or ""
        if is_adaptive_context_export_filename(fn):
            lines.append(
                f"{i}. {fn} (skipped — use **`/adaptive-status`** in DMs and reply to that message with this file to replace adaptive context)"
            )
            continue
        file_type = FileProcessor.get_file_type(fn)
        lines.append(f"{i}. {fn} ({file_type} file, {len(attachment['data'])} bytes)")
        if file_type == "image":
            try:
                images.append(FileProcessor.prepare_image_for_llm(attachment["data"], fn)["data"])
            except Exception as e:
                lines.append(f"   ⚠️ Failed to process image: {str(e)}")
    return "\n".join(lines) + "\n", images


async def _build_chat_system_prompt(
    user_id, channel_id, message_text, platform, *, adaptive_dm, is_dm, fast_reply, image_gen_capability_note
) -> str:
    today, now_hm = integrations.update_system_time_date()
    location, city, country = await integrations.get_location_cached()
    # Adaptive DM mode uses a minimal base + user-built context only (no global persona).
    persona = ADAPTIVE_DM_BASE_PERSONA if adaptive_dm else persona_manager.get_persona(get_function_persona_name("chat"))
    fields = dict(
        date=today,
        time=now_hm,
        location=location,
        city=city,
        country=country,
        platform=platform.capitalize(),
        command_count=len(command_db.commands),
        command_list=command_db.get_all_commands_formatted(),
        command_suggestions=_command_suggestions(message_text),
    )
    enhanced = get_enhanced_prompt("chat_adaptive_dm" if adaptive_dm else "chat", **fields)
    if adaptive_dm and not enhanced.strip():
        enhanced = get_enhanced_prompt("chat", **fields)
    prompt = f"{persona}\n\n{enhanced}"

    if adaptive_dm:
        profile = adaptive_dm_manager.get_profile_prompt(user_id)
        if profile:
            prompt += f"\n\n{profile}"
        llm_prof = conversation_manager.get_dm_profile_llm(channel_id)
        if llm_prof:
            prompt += (
                "\n\nBrief user profile (for tone/personalization only; do not mention unless clearly relevant):\n"
                f"{llm_prof}"
            )
        prompt += ADAPTIVE_DM_SYSTEM_SUFFIX
    elif is_dm:
        prompt += (
            "\n\nThis is a direct DM chat. Use a relaxed, natural tone. "
            "Keep it human and concise, and avoid overly formal phrasing."
        )
    if is_dm and _has_image_model(user_id):
        prompt += "\n\n" + (image_gen_capability_note or (_ADAPTIVE_IMAGE_NOTE if adaptive_dm else _DM_IMAGE_NOTE))
    if fast_reply:
        prompt += (
            "\n\nFast reply mode is enabled. Be concise and quick: "
            "usually 1-3 short sentences unless the user asks for details."
        )
    return prompt


async def _history_messages(user_id, channel_id, username, *, is_continuation, is_dm, adaptive_dm) -> List[Dict[str, Any]]:
    """Persisted user/assistant turns to send (plus older-topic notes for DMs). May compact/reset storage."""
    if is_continuation:
        if is_dm:
            try:
                await compact_dm_history_for_channel(user_id, channel_id, username, force=False)
            except Exception as e:
                print(f"⚠️ DM history compaction failed: {e}", flush=True)
        history = conversation_manager.get_conversation(channel_id)
        if adaptive_dm:
            history = conversation_manager.roll_adaptive_dm_transcript_messages(channel_id, history)
    else:
        if is_dm:
            # New DM session: drop the rolling transcript but keep topic/profile memory.
            conversation_manager.reset_dm_transcript_only(channel_id)
        else:
            conversation_manager.clear_conversation(channel_id)
        history = []

    rolling = [m for m in (history or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    out: List[Dict[str, Any]] = []
    if is_dm:
        rolling = rolling[-13:]  # at most 13 turns before the new user message
        dm_summary = conversation_manager.get_dm_summary_text(channel_id)
        if dm_summary:
            out.append({
                "role": "system",
                "content": (
                    "Older DM context (topic notes; may be stale—use only when clearly relevant; "
                    "do not bring up unrelated past topics):\n"
                    f"{dm_summary}"
                ),
            })
    return out + rolling


def _persisted_user_message(username, message_text, reply_context_block) -> str:
    msg = f"{username} says: {message_text}"
    rcb = (reply_context_block or "").strip()
    return f"{rcb}\n\n{msg}" if rcb else msg


def finalize_reply(user_id, response_text: str, *, is_dm: bool) -> str:
    """Normalize raw model text into what the user sees."""
    text = clean_response(response_text or "")
    if is_dm and adaptive_dm_manager.is_enabled(user_id) and _has_image_model(user_id):
        text = strip_leaked_image_placeholders(text)
    return text


def persist_turn(
    user_id,
    channel_id,
    username,
    message_text,
    response_text: str,
    *,
    is_dm: bool = False,
    reply_context_block: Optional[str] = None,
) -> None:
    """Store a user/assistant exchange in the channel transcript (skips failures and news echoes)."""
    if not response_text or is_error_reply(response_text):
        return
    adaptive_dm = bool(is_dm and adaptive_dm_manager.is_enabled(user_id))
    user_message = _persisted_user_message(username, message_text, reply_context_block)
    if adaptive_dm and not (reply_context_block or "").strip() and is_news_style_dm_bot_text(user_message):
        return
    conversation_manager.add_message(channel_id, "user", user_message)
    conversation_manager.add_message(channel_id, "assistant", response_text)
    conversation_manager.save()
    if adaptive_dm:
        schedule_dm_adaptive_background_tasks(user_id, channel_id)


async def ask_llm(
    user_id,
    channel_id,
    message_text,
    username,
    is_continuation=False,
    platform="discord",
    chat_context=None,
    attachments=None,
    is_dm=False,
    fast_reply=False,
    reply_context_block: Optional[str] = None,
    image_gen_capability_note: Optional[str] = None,
    *,
    persist: bool = True,
    abort_check: Optional[Callable[[], Awaitable[bool]]] = None,
):
    """Main chat entry point. With persist=False the caller must call persist_turn() after delivering."""
    if abort_check and await abort_check():
        return ""
    adaptive_dm = bool(is_dm and adaptive_dm_manager.is_enabled(user_id))
    # Adaptive DM keeps the user's default chat model, not the per-function chat override.
    requested_model, provider = default_model(user_id) if adaptive_dm else function_model(user_id, "chat")

    system_prompt = await _build_chat_system_prompt(
        user_id,
        channel_id,
        message_text,
        platform,
        adaptive_dm=adaptive_dm,
        is_dm=is_dm,
        fast_reply=fast_reply,
        image_gen_capability_note=image_gen_capability_note,
    )
    history = await _history_messages(
        user_id, channel_id, username, is_continuation=is_continuation, is_dm=is_dm, adaptive_dm=adaptive_dm
    )

    # Prefix every user message with who said it (no wake word in content)
    if platform == "discord" and chat_context:
        formatted_message = _format_discord_message(username, message_text, chat_context)
    else:
        formatted_message = f"{username} says: {message_text}"
    rcb = (reply_context_block or "").strip()
    if rcb:
        formatted_message = f"{rcb}\n\n{formatted_message}"
    attachment_text, images = _attachment_context(attachments)
    user_msg: Dict[str, Any] = {"role": "user", "content": formatted_message + attachment_text}
    if images:
        user_msg["images"] = images  # always attach so fallback can pick a vision model

    messages = [{"role": "system", "content": system_prompt}, *history, user_msg]
    _, response_text = await try_models_with_fallback(
        requested_model,
        messages,
        images=bool(images),
        provider=provider,
        request_options={"num_predict": 220, "temperature": 0.55} if fast_reply else None,
        abort_check=abort_check,
    )
    if abort_check and await abort_check():
        return ""
    response_text = finalize_reply(user_id, response_text, is_dm=is_dm)
    if persist:
        persist_turn(
            user_id, channel_id, username, message_text, response_text, is_dm=is_dm, reply_context_block=reply_context_block
        )
    return response_text


async def plan_command_from_text(user_id: int, message_text: str, command_schema: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Infer a slash command plan from natural language in DMs."""
    if not message_text or not command_schema:
        return {"should_execute": False}
    schema_json = json.dumps(command_schema, ensure_ascii=True)
    planner_prompt = get_enhanced_prompt("command_planner_adaptive", schema_json=schema_json, user_message=message_text)
    requested_model, provider = function_model(user_id, "command_planner")
    _, raw = await try_models_with_fallback(
        requested_model,
        messages=[{"role": "system", "content": function_system("command_planner", planner_prompt)}],
        images=False,
        provider=provider,
        request_options={"num_predict": 320, "temperature": 0.1},
    )
    parsed = extract_json_object(raw or "")
    if not parsed:
        return {"should_execute": False}
    plan = {
        "should_execute": bool(parsed.get("should_execute", False)),
        "command": str(parsed.get("command", "") or "").strip().lower(),
        "arguments": parsed.get("arguments", {}) if isinstance(parsed.get("arguments", {}), dict) else {},
        "reason": str(parsed.get("reason", "") or "").strip(),
        "risk": str(parsed.get("risk", "safe") or "safe").strip().lower(),
    }
    return plan


def _compact_dm_tail_for_image_flow(channel_id: int, username: str, max_chars: int = 1100) -> str:
    """Recent transcript slice for image flow — no extra LLM; keeps token budget low."""
    hist = conversation_manager.roll_adaptive_dm_transcript_messages(channel_id) or []
    chunks: List[str] = []
    for m in hist[-10:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        role = "U" if m.get("role") == "user" else "A"
        text = str(m.get("content", "") or "").strip().replace("\n", " ")
        if not text:
            continue
        if len(text) > 280:
            text = text[:260].rstrip() + "…"
        chunks.append(f"{role}: {text}")
    joined = " | ".join(chunks)
    if len(joined) > max_chars:
        joined = "…" + joined[-(max_chars - 1) :]
    return joined or f"U: {username} (no prior transcript)"


async def adaptive_dm_image_flow_draft_reply(
    user_id: int,
    channel_id: int,
    username: str,
    user_trigger: str,
) -> str:
    """
    Step 1: Chat model writes the DM reply as if the image were already attached (token-light).
    """
    requested_model, provider = default_model(user_id)
    tail = _compact_dm_tail_for_image_flow(channel_id, username)
    sys = (
        f"{ADAPTIVE_DM_BASE_PERSONA}\n"
        "The user asked for a generated image. You already attached the image to this reply—write ONLY your "
        "Discord message text as you normally would (no meta, no 'here is the image', no apologies about being text-only). "
        "Stay concise (max ~120 words). Plain text."
    )
    prof = adaptive_dm_manager.get_profile_prompt(user_id)
    if prof:
        sys = sys + "\n\n" + prof[:800]
    user_block = (
        f"Recent DM (compact): {tail}\n"
        f"User now: {(user_trigger or '')[:500]}"
    )
    messages = [{"role": "system", "content": sys}, {"role": "user", "content": user_block}]
    try:
        _, raw = await try_models_with_fallback(
            requested_model,
            messages,
            images=False,
            provider=provider,
            request_options={"num_predict": 320, "temperature": 0.65},
        )
        out = clean_response(raw or "")
        if out and not out.startswith("Error:") and not out.startswith("⚠️"):
            return out[:1800]
    except Exception:
        pass
    return "Here’s a visual for what we discussed."


async def adaptive_dm_image_flow_compress_image_prompt(
    user_id: int,
    draft_reply: str,
    user_trigger: str,
) -> str:
    """Step 2: One short line optimized for the image model (matches draft + request)."""
    requested_model, provider = default_model(user_id)
    prompt = (
        "Output ONE English image prompt only — the exact string that will be sent to an image API. "
        "No quotes, no prefixes like 'Image prompt:', no labels, no second paragraph. Max 60 words.\n"
        "Must match the assistant reply below and the user's ask. Photoreal or style implied by context.\n\n"
        f"User ask: {(user_trigger or '')[:400]}\n"
        f"Assistant reply (ground truth): {(draft_reply or '')[:900]}"
    )
    try:
        _, raw = await try_models_with_fallback(
            requested_model,
            [{"role": "user", "content": prompt}],
            images=False,
            provider=provider,
            request_options={"num_predict": 180, "temperature": 0.45},
        )
        out = clean_response(raw or "")
        if out and not out.startswith("Error:") and not out.startswith("⚠️"):
            return out[:1200]
    except Exception:
        pass
    combined = f"{(user_trigger or '')[:200]}. {(draft_reply or '')[:400]}"
    return combined[:900]


async def adaptive_dm_image_flow_refine_text_for_image(
    user_id: int,
    draft_reply: str,
    image_bytes: bytes,
    *,
    image_prompt: str = "",
) -> str:
    """Step 3: Vision — JSON only so we never concatenate image descriptions into the DM text."""
    if not image_bytes:
        return draft_reply or ""
    requested_model, provider = default_model(user_id)
    try:
        b64 = base64.b64encode(image_bytes).decode("ascii")
    except Exception:
        return draft_reply or ""
    sys = (
        "You see the image that will attach to a Discord DM. You also see the draft reply text.\n"
        "Return JSON ONLY (no markdown fences, no prose before/after). Exactly one of:\n"
        '{"keep":true}\n'
        'or\n'
        '{"keep":false,"message":"<full replacement DM text for the user>"}\n'
        "Use keep:true if the image matches what the draft describes. Use keep:false only if the image is wrong enough "
        "that the text must change; then message is the full new user-visible reply (max ~900 chars, plain text).\n"
        "Never include: internal image prompts, bracket tags like [Sent a generated image: ...], "
        "or a second paragraph describing the image for the user — the image is shown separately.\n"
        "Never output text that only describes the image in brackets — that is invalid."
    )
    user_t = f"Draft:\n{(draft_reply or '')[:900]}"
    messages = [{"role": "system", "content": sys}, {"role": "user", "content": user_t, "images": [b64]}]
    try:
        _, raw = await try_models_with_fallback(
            requested_model,
            messages,
            images=True,
            provider=provider,
            request_options={"num_predict": 280, "temperature": 0.25},
        )
        out = clean_response(raw or "")
        if not out or is_error_reply(out):
            return draft_reply or ""
        parsed = extract_json_object(out)
        if parsed.get("keep") is True:
            return draft_reply or ""
        msg = parsed.get("message")
        if isinstance(msg, str) and msg.strip():
            m = msg.strip()
            if re.match(r"^\s*\[Sent a generated image", m, flags=re.IGNORECASE):
                return draft_reply or ""
            return m[:1800]
        # Legacy plain KEEP
        if out.strip().upper() in {"KEEP", "KEEP.", "KEEP!"}:
            return draft_reply or ""
        return draft_reply or ""
    except Exception:
        return draft_reply or ""


async def ask_llm_shitpost(user_id: int, word: str) -> str:
    """One-shot LLM reply for shitpost: max 2 words confirming/continuing the word. Returns empty on error."""
    system_prompt = get_enhanced_prompt("shitpost")
    requested_model, provider = function_model(user_id, "shitpost")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": word},
    ]
    try:
        _, response_text = await try_models_with_fallback(
            requested_model,
            messages,
            images=False,
            provider=provider,
        )
        cleaned = clean_response(response_text or "")
        if is_error_reply(cleaned):
            return ""
        parts = cleaned.split()
        return " ".join(parts[:2]) if parts else ""
    except Exception:
        return ""


def _format_discord_message(username, message, context):
    """Format Discord message with chat context; prefix with who is speaking."""
    if not context or len(context) == 0:
        return f"{username} says: {message}"
    context_str = "\nRecent messages in this channel:\n"
    for msg in context[-10:]:
        context_str += f"{msg['author']}: {msg['content']}\n"
    return f"{context_str}\n{username} says: {message}"


__all__ = [
    "SUPPORTED_FILE_TYPES",
    "adaptive_dm_image_flow_compress_image_prompt",
    "adaptive_dm_image_flow_draft_reply",
    "adaptive_dm_image_flow_refine_text_for_image",
    "analyze_file",
    "ask_llm",
    "ask_llm_shitpost",
    "clean_response",
    "clear_vision_model_cache",
    "command_db",
    "compact_dm_history_for_channel",
    "compare_files",
    "extract_json_object",
    "extract_json_object_loose",
    "finalize_reply",
    "get_enhanced_prompt",
    "merge_adaptive_manual_guidance_into_profile",
    "persist_turn",
    "plan_command_from_text",
    "probe_model",
    "strip_leaked_agent_tool_artifacts",
    "strip_leaked_image_placeholders",
    "try_models_with_fallback",
    "validate_and_set_function_model",
    "validate_and_set_image_generation_model",
    "validate_and_set_model",
]
