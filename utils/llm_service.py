import re
import json
import os
import base64
import mimetypes
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
import integrations
from integrations import update_system_time_date
from conversations import (
    conversation_manager,
    is_news_style_dm_bot_text,
    is_slash_command_bot_turn,
    strip_discord_recent_context_block,
)
from personas import persona_manager
from models import model_manager
from llm_function_prefs import get_function_persona_name
from utils.openrouter_image import probe_openrouter_image_model
from adaptive_dm import (
    adaptive_dm_manager,
    ADAPTIVE_DM_BASE_PERSONA,
    ADAPTIVE_DM_SYSTEM_SUFFIX,
    is_adaptive_context_export_filename,
)
from utils import home_log
from utils import dm_background
from utils.llm import providers

def _get_fallback_chain():
    from utils.model_fallback import get_fallback_chain
    return get_fallback_chain()

_system_prompts_cache = None

def _get_system_prompts():
    global _system_prompts_cache
    if _system_prompts_cache is None:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "system_prompts.json")
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

# File support configuration
SUPPORTED_FILE_TYPES = {
    'image': ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'],
    'text': ['.txt', '.json', '.csv', '.xml', '.yaml', '.yml', '.md', '.log'],
    'code': ['.py', '.js', '.html', '.css', '.java', '.cpp', '.c', '.go', '.rs', '.php', '.sh'],
    'document': ['.pdf'],
}
SUPPORTED_EXTENSION_TO_TYPE = {
    ext: file_type
    for file_type, extensions in SUPPORTED_FILE_TYPES.items()
    for ext in extensions
}

from utils.llm.command_db import command_db  # noqa: E402  (re-exported for callers)

class FileProcessor:
    """Process and analyze different types of files"""

    @staticmethod
    def get_file_type(filename: str) -> str:
        """Get the type of file based on extension"""
        ext = os.path.splitext((filename or "").lower())[1]
        return SUPPORTED_EXTENSION_TO_TYPE.get(ext, "unknown")

    @staticmethod
    def read_text_file(content: bytes) -> str:
        """Decode text bytes (UTF-8, then Latin-1)."""
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1")

    @staticmethod
    def extract_text(filename: str, content: bytes) -> str:
        """Plain text for text/code files and PDFs (via pypdf)."""
        if (filename or "").lower().endswith(".pdf"):
            import io
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            text = "\n\n".join(p for p in pages if p)
            return text or "[No extractable text in this PDF — it may be scanned images.]"
        return FileProcessor.read_text_file(content)

    @staticmethod
    def prepare_image_for_llm(image_data: bytes, filename: str) -> Dict:
        """Prepare image data for LLM with vision capabilities"""
        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        return {
            "data": base64.b64encode(image_data).decode("utf-8"),
            "mime_type": mime_type,
            "filename": filename,
        }

    @staticmethod
    def default_analysis_prompt(filename: str, file_type: str) -> str:
        """Instruction used when the user gave no prompt (file content is appended separately)."""
        if file_type == "text":
            return f"Analyze the text file '{filename}'. Provide: 1) Summary, 2) Key points, 3) Any issues or suggestions"
        if file_type == "code":
            return (
                f"Review the code file '{filename}'. Provide: 1) What the code does, 2) Potential bugs or issues, "
                "3) Suggestions for improvement, 4) Security concerns if any"
            )
        if file_type == "image":
            return (
                f"Analyze this image file: '{filename}'. Describe what you see in detail, including any text, "
                "objects, colors, and context. If there's text in the image, transcribe it."
            )
        if file_type == "document":
            return f"Analyze the document '{filename}'. Provide: 1) Document type, 2) Main topics, 3) Key information, 4) Summary"
        return f"Analyze this file '{filename}'. Provide information about what type of file it appears to be and any observations."


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _extract_json_object_loose(text: str) -> Dict[str, Any]:
    """Parse first JSON object in model output; tolerates preamble/postamble."""
    t = _clean_response(text or "").strip()
    if not t:
        return {}
    parsed = _extract_json_object(t)
    if parsed:
        return parsed
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end > start:
        try:
            chunk = t[start : end + 1]
            parsed = json.loads(chunk)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def _normalize_merge_profile_dict(parsed: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Build profile dict from LLM output; missing keys fall back to current."""
    cur = current if isinstance(current, dict) else {}
    pn = parsed.get("preferred_name")
    if pn is None:
        pn = cur.get("preferred_name", "")
    likes = parsed.get("likes")
    if not isinstance(likes, list):
        likes = cur.get("likes", []) or []
    dislikes = parsed.get("dislikes")
    if not isinstance(dislikes, list):
        dislikes = cur.get("dislikes", []) or []
    tones = parsed.get("tone_notes")
    if not isinstance(tones, list):
        tones = cur.get("tone_notes", []) or []
    return {
        "preferred_name": str(pn or "").strip(),
        "likes": [str(x).strip() for x in likes if str(x).strip()][:24],
        "dislikes": [str(x).strip() for x in dislikes if str(x).strip()][:24],
        "tone_notes": [str(x).strip() for x in tones if str(x).strip()][:30],
    }


def _heuristic_merge_manual_into_profile(current: Dict[str, Any], guidance: str) -> Dict[str, Any]:
    """Last-resort merge when the LLM does not return JSON: fold trimmed lines into tone_notes / simple patterns."""
    base = _normalize_merge_profile_dict({}, current)
    text = (guidance or "").replace("\r\n", "\n")
    lines = []
    for ln in text.split("\n"):
        s = ln.strip().lstrip("-•*").strip()
        if len(s) < 2:
            continue
        lines.append(s[:200])
    if not lines:
        return base
    pn = base["preferred_name"]
    m_name = re.search(r"\bcall me ([a-zA-Z0-9_\- ]{2,30})", text, flags=re.IGNORECASE)
    if m_name:
        pn = m_name.group(1).strip()
    likes = list(base["likes"])
    dislikes = list(base["dislikes"])
    tones = list(base["tone_notes"])
    for s in lines[:40]:
        low = s.lower()
        if re.match(r"^(i like|i love|my favorite is)\b", low):
            val = re.sub(r"^(i like|i love|my favorite is)\s*", "", low, flags=re.IGNORECASE).strip(" .,!?:;")[:80]
            if val and val not in likes:
                likes.append(val)
        elif re.match(r"^(i dislike|i hate|i don't like|i dont like)\b", low):
            val = re.sub(r"^(i dislike|i hate|i don't like|i dont like)\s*", "", low, flags=re.IGNORECASE).strip(" .,!?:;")[:80]
            if val and val not in dislikes:
                dislikes.append(val)
        else:
            if s not in tones and s.lower() not in {t.lower() for t in tones}:
                tones.append(s)
    return {
        "preferred_name": pn,
        "likes": likes[-24:],
        "dislikes": dislikes[-24:],
        "tone_notes": tones[-30:],
    }


def _dm_summary_line_from_message(item: Dict[str, Any]) -> str:
    role = str(item.get("role", "user") or "user")
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else None
    if role == "assistant" and is_slash_command_bot_turn(meta):
        return ""
    if role == "assistant" and meta and meta.get("news_delivery"):
        return ""
    content = str(item.get("content", "") or "").strip()
    if not content:
        return ""
    if role == "assistant" and is_news_style_dm_bot_text(content):
        return ""
    content = strip_discord_recent_context_block(content)
    lower = content.lower()
    if any(
        marker in lower
        for marker in [
            "news briefing",
            "daily digest",
            "breaking news",
            "top stories",
            "rss",
            "source:",
            "headline:",
        ]
    ):
        return ""
    cap = 160 if role == "user" else 100
    if len(content) > cap:
        content = content[: cap - 1].rstrip() + "…"
    return f"{role}: {content}"


def _dm_build_old_transcript_chunk(old_messages: List[Dict[str, Any]], max_lines: int = 36) -> str:
    old_text: List[str] = []
    for item in old_messages[-72:]:
        line = _dm_summary_line_from_message(item)
        if line:
            old_text.append(line)
        if len(old_text) >= max_lines:
            break
    return "\n".join(old_text).strip()


async def _dm_llm_merge_topic_summaries(
    user_id: int,
    channel_id: int,
    joined_old_transcript: str,
) -> None:
    """Background: merge rolled-off DM lines into per-topic summaries (JSON)."""
    if not joined_old_transcript.strip():
        return
    conversation_manager.prune_stale_dm_topics(channel_id)
    existing = conversation_manager.get_dm_topics(channel_id)
    eff = model_manager.get_effective_model_for_function(user_id, "dm_summary")
    requested_model = eff.get("model", "qwen2.5:7b")
    provider = eff.get("provider", "local")
    sys_p = get_enhanced_prompt("dm_summary_topics")
    persona_key = get_function_persona_name("dm_summary")
    base = persona_manager.get_persona(persona_key).strip()
    full_sys = f"{base}\n\n{sys_p}" if base else sys_p
    user_block = (
        f"existing_topics_json:\n{json.dumps(existing[-14:], ensure_ascii=False)}\n\n"
        f"older_transcript:\n{joined_old_transcript[:12000]}"
    )
    messages = [{"role": "system", "content": full_sys}, {"role": "user", "content": user_block}]
    try:
        _, raw = await _try_models_with_fallback(
            requested_model,
            messages,
            images=False,
            provider=provider,
            request_options={"num_predict": 420, "temperature": 0.2},
        )
    except Exception:
        return
    parsed = _extract_json_object_loose(raw or "")
    topics_out = parsed.get("topics")
    if not isinstance(topics_out, list):
        return
    now = time.time()
    by_id: Dict[str, Dict[str, Any]] = {str(t.get("id", "")): t for t in existing if str(t.get("id", "")).strip()}
    for item in topics_out:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id", "") or "").strip() or f"t{int(now)}_{len(by_id)}"
        label = str(item.get("label", "") or "").strip()[:120]
        summary = str(item.get("summary", "") or "").strip()
        if not summary:
            continue
        try:
            last_ts = float(item.get("last_ts", now))
        except (TypeError, ValueError):
            last_ts = now
        by_id[tid] = {"id": tid[:64], "label": (label or tid)[:120], "summary": summary[:900], "last_ts": last_ts}
    merged = sorted(by_id.values(), key=lambda x: float(x.get("last_ts", 0)), reverse=True)
    conversation_manager.set_dm_topics(channel_id, merged)
    conversation_manager.save()


async def _dm_llm_refresh_brief_profile(user_id: int, channel_id: int) -> None:
    """Background: refresh dm_profile_llm from recent user lines (separate from adaptive structured profile)."""
    hist_raw = conversation_manager.get_conversation(channel_id) or []
    hist = (
        conversation_manager.roll_adaptive_dm_transcript_messages(channel_id, hist_raw)
        if adaptive_dm_manager.is_enabled(user_id)
        else hist_raw
    )
    user_bits: List[str] = []
    for m in hist[-14:]:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        t = str(m.get("content", "") or "").strip()
        if not t:
            continue
        t = re.sub(r"^[\w.\- ]+\s+says:\s*", "", t, flags=re.IGNORECASE).strip()
        if len(t) > 220:
            t = t[:218] + "…"
        user_bits.append(t)
    if not user_bits:
        return
    prev = conversation_manager.get_dm_profile_llm(channel_id)
    eff = model_manager.get_effective_model_for_function(user_id, "dm_summary")
    requested_model = eff.get("model", "qwen2.5:7b")
    provider = eff.get("provider", "local")
    sys_p = get_enhanced_prompt("dm_user_profile_brief")
    persona_key = get_function_persona_name("dm_summary")
    base = persona_manager.get_persona(persona_key).strip()
    full_sys = f"{base}\n\n{sys_p}" if base else sys_p
    payload = "previous_profile:\n" + (prev or "(none)") + "\n\nrecent_user_lines:\n" + "\n---\n".join(user_bits[-8:])
    try:
        _, raw = await _try_models_with_fallback(
            requested_model,
            [{"role": "system", "content": full_sys}, {"role": "user", "content": payload[:8000]}],
            images=False,
            provider=provider,
            request_options={"num_predict": 200, "temperature": 0.15},
        )
    except Exception:
        return
    out = _clean_response(raw or "").strip()
    if not out or out.startswith("Error:") or out.startswith("⚠️"):
        return
    conversation_manager.set_dm_profile_llm(channel_id, out)
    conversation_manager.save()


def schedule_dm_adaptive_background_tasks(user_id: int, channel_id: int) -> None:
    """After an adaptive DM reply: profile refresh in background (history merge is scheduled separately)."""
    if not adaptive_dm_manager.is_enabled(user_id):
        return
    conversation_manager.set_dm_adaptive_user(channel_id, user_id)
    dm_background.spawn(_dm_llm_refresh_brief_profile(user_id, channel_id), name=f"dm_prof_{channel_id}")


async def compact_dm_history_for_channel(
    user_id: int, channel_id: int, username: str, force: bool = False
) -> Dict[str, Any]:
    """Trim DM transcript aggressively; merge rolled-off lines into topic summaries (LLM in background unless force)."""
    adaptive = adaptive_dm_manager.is_enabled(user_id)
    conversation = (
        conversation_manager.roll_adaptive_dm_transcript_messages(channel_id)
        if adaptive
        else (conversation_manager.get_conversation(channel_id) or [])
    )
    cutoff = conversation_manager.get_dm_history_cutoff(channel_id, default_cutoff=10)
    max_messages = max(6, cutoff * 2)
    if not force and len(conversation) <= max_messages:
        return {"compacted": False, "reason": "under-cutoff", "cutoff": cutoff}
    if len(conversation) <= 4:
        return {"compacted": False, "reason": "not-enough-messages", "cutoff": cutoff}

    if len(conversation) > max_messages:
        old_messages = conversation[:-max_messages]
        recent_messages = conversation[-max_messages:]
    elif force and len(conversation) > 6:
        old_messages = conversation[:-6]
        recent_messages = conversation[-6:]
    else:
        return {"compacted": False, "reason": "nothing-to-compact", "cutoff": cutoff}

    if not old_messages:
        return {"compacted": False, "reason": "nothing-to-compact", "cutoff": cutoff}

    joined = _dm_build_old_transcript_chunk(old_messages, max_lines=40)
    if not joined.strip():
        conversation_manager.replace_conversation(channel_id, recent_messages)
        conversation_manager.save()
        return {
            "compacted": True,
            "cutoff": cutoff,
            "merged_messages": len(old_messages),
            "remaining_messages": len(recent_messages),
            "reason": "empty-source-trimmed",
        }

    conversation_manager.replace_conversation(channel_id, recent_messages)
    conversation_manager.save()

    if force:
        await _dm_llm_merge_topic_summaries(user_id, channel_id, joined)
        return {
            "compacted": True,
            "cutoff": cutoff,
            "merged_messages": len(old_messages),
            "remaining_messages": len(recent_messages),
        }

    dm_background.spawn(
        _dm_llm_merge_topic_summaries(user_id, channel_id, joined),
        name=f"dm_sum_{channel_id}",
    )
    return {
        "compacted": True,
        "cutoff": cutoff,
        "merged_messages": len(old_messages),
        "remaining_messages": len(recent_messages),
        "async": True,
    }


async def plan_command_from_text(user_id: int, message_text: str, command_schema: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Infer a slash command plan from natural language in DMs."""
    if not message_text or not command_schema:
        return {"should_execute": False}
    schema_json = json.dumps(command_schema, ensure_ascii=True)
    planner_prompt = get_enhanced_prompt("command_planner_adaptive", schema_json=schema_json, user_message=message_text)
    eff = model_manager.get_effective_model_for_function(user_id, "command_planner")
    requested_model = eff.get("model", "qwen2.5:7b")
    provider = eff.get("provider", "local")
    persona_key = get_function_persona_name("command_planner")
    base = persona_manager.get_persona(persona_key).strip()
    sys_parts = [base, planner_prompt] if base else [planner_prompt]
    _, raw = await _try_models_with_fallback(
        requested_model,
        messages=[{"role": "system", "content": "\n\n".join(sys_parts)}],
        images=False,
        provider=provider,
        request_options={"num_predict": 320, "temperature": 0.1},
    )
    parsed = _extract_json_object(raw or "")
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
    model_info = model_manager.get_user_model_info(user_id)
    requested_model = model_info.get("model", "llama3.2:1b")
    provider = model_info.get("provider", "local")
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
        _, raw = await _try_models_with_fallback(
            requested_model,
            messages,
            images=False,
            provider=provider,
            request_options={"num_predict": 320, "temperature": 0.65},
        )
        out = _clean_response(raw or "")
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
    model_info = model_manager.get_user_model_info(user_id)
    requested_model = model_info.get("model", "llama3.2:1b")
    provider = model_info.get("provider", "local")
    prompt = (
        "Output ONE English image prompt only — the exact string that will be sent to an image API. "
        "No quotes, no prefixes like 'Image prompt:', no labels, no second paragraph. Max 60 words.\n"
        "Must match the assistant reply below and the user's ask. Photoreal or style implied by context.\n\n"
        f"User ask: {(user_trigger or '')[:400]}\n"
        f"Assistant reply (ground truth): {(draft_reply or '')[:900]}"
    )
    try:
        _, raw = await _try_models_with_fallback(
            requested_model,
            [{"role": "user", "content": prompt}],
            images=False,
            provider=provider,
            request_options={"num_predict": 180, "temperature": 0.45},
        )
        out = _clean_response(raw or "")
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
    model_info = model_manager.get_user_model_info(user_id)
    requested_model = model_info.get("model", "llama3.2:1b")
    provider = model_info.get("provider", "local")
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
        _, raw = await _try_models_with_fallback(
            requested_model,
            messages,
            images=True,
            provider=provider,
            request_options={"num_predict": 280, "temperature": 0.25},
        )
        out = _clean_response(raw or "")
        if not out or out.startswith("Error:") or out.startswith("⚠️"):
            return draft_reply or ""
        parsed = _extract_json_object(out)
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
    reuse_response: Optional[str] = None,
):
    """Main LLM interface for all platforms with file support"""
    if abort_check and await abort_check():
        return ""
    # Get system info
    date, time = update_system_time_date()
    location, city, country = await integrations.get_location_cached()
    
    # Persona: adaptive DM mode uses a minimal base + user-built context only (no global persona).
    adaptive_dm = bool(is_dm and adaptive_dm_manager.is_enabled(user_id))
    if adaptive_dm:
        system_prompt = ADAPTIVE_DM_BASE_PERSONA
    else:
        fn_persona = get_function_persona_name("chat")
        system_prompt = persona_manager.get_persona(fn_persona)
    
    # Get model (adaptive DM keeps the user's default chat model, not per-function chat override)
    if adaptive_dm:
        model_info = model_manager.get_user_model_info(user_id)
        requested_model = model_info.get("model", "llama3.2:1b")
        provider = model_info.get("provider", "local")
    else:
        eff = model_manager.get_effective_model_for_function(user_id, "chat")
        requested_model = eff.get("model", "llama3.2:1b")
        provider = eff.get("provider", "local")
    
    # Check if user is asking for commands or help
    user_message_lower = message_text.lower()
    is_command_related = any(keyword in user_message_lower for keyword in [
        "command", "help", "what can you do", "how do i", "how to", 
        "show me", "list", "available", "options", "features"
    ])
    
    # Get command suggestions if needed
    command_suggestions = ""
    if is_command_related or "?" in user_message_lower:
        suggestions = command_db.get_suggestions(message_text, limit=3)
        if suggestions:
            command_suggestions = "\n\nIf you're looking for commands, here are some suggestions:\n"
            for suggestion in suggestions:
                command_suggestions += f"- `/{suggestion['name']}`: {suggestion['description']}\n"
            command_suggestions += "\nUse `/help` to see all available commands or `/help [search]` to search."
    
    # Process attachments if any
    attachment_context = ""
    images_data = []
    
    if attachments:
        attachment_context = "\n\nUser has uploaded the following files:\n"
        for i, attachment in enumerate(attachments, 1):
            fn = attachment.get("filename") or ""
            if is_adaptive_context_export_filename(fn):
                attachment_context += (
                    f"{i}. {fn} (skipped — use **`/adaptive-status`** in DMs and reply to that message with this file to replace adaptive context)\n"
                )
                continue
            file_type = FileProcessor.get_file_type(fn)
            attachment_context += f"{i}. {fn} ({file_type} file, {len(attachment['data'])} bytes)\n"
            
            # Prepare images for vision models
            if file_type == "image":
                try:
                    img_data = FileProcessor.prepare_image_for_llm(attachment['data'], fn)
                    images_data.append(img_data)
                except Exception as e:
                    attachment_context += f"   ⚠️ Failed to process image: {str(e)}\n"
    
    # Prefix every user message with who said it (no wake word in content)
    if platform == "discord" and chat_context:
        formatted_message = _format_discord_message(username, message_text, chat_context)
    else:
        formatted_message = f"{username} says: {message_text}"
    persisted_user_message = f"{username} says: {message_text}"
    rcb = (reply_context_block or "").strip()
    if rcb:
        formatted_message = f"{rcb}\n\n{formatted_message}"
        persisted_user_message = f"{rcb}\n\n{persisted_user_message}"

    if reuse_response is not None:
        response_text = _clean_response(str(reuse_response or ""))
        if adaptive_dm and str(
            model_manager.get_effective_model_for_function(user_id, "image_generation").get("model") or ""
        ).strip():
            response_text = _strip_leaked_image_placeholders(response_text)
        if (
            persist
            and response_text
            and not response_text.startswith("Error:")
        ):
            skip_adaptive_store = bool(
                adaptive_dm
                and not (reply_context_block or "").strip()
                and is_news_style_dm_bot_text(persisted_user_message)
            )
            if not skip_adaptive_store:
                conversation_manager.add_message(channel_id, "user", persisted_user_message)
                conversation_manager.add_message(channel_id, "assistant", response_text)
                conversation_manager.save()
                if adaptive_dm:
                    schedule_dm_adaptive_background_tasks(user_id, channel_id)
        return response_text

    # Add attachment context to message
    if attachment_context:
        formatted_message += attachment_context
    
    chat_prompt_key = "chat_adaptive_dm" if adaptive_dm else "chat"
    enhanced = get_enhanced_prompt(
        chat_prompt_key,
        date=date,
        time=time,
        location=location,
        city=city,
        country=country,
        platform=platform.capitalize(),
        command_count=len(command_db.commands),
        command_list=command_db.get_all_commands_formatted(),
        command_suggestions=command_suggestions or "",
    )
    if adaptive_dm and not (enhanced or "").strip():
        enhanced = get_enhanced_prompt(
            "chat",
            date=date,
            time=time,
            location=location,
            city=city,
            country=country,
            platform=platform.capitalize(),
            command_count=len(command_db.commands),
            command_list=command_db.get_all_commands_formatted(),
            command_suggestions=command_suggestions or "",
        )
    enhanced_system_prompt = f"{system_prompt}\n\n{enhanced}"
    if adaptive_dm:
        dm_profile_prompt = adaptive_dm_manager.get_profile_prompt(user_id)
        if dm_profile_prompt:
            enhanced_system_prompt = f"{enhanced_system_prompt}\n\n{dm_profile_prompt}"
        llm_prof = conversation_manager.get_dm_profile_llm(channel_id)
        if llm_prof:
            enhanced_system_prompt += (
                "\n\nBrief user profile (for tone/personalization only; do not mention unless clearly relevant):\n"
                f"{llm_prof}"
            )
        enhanced_system_prompt += ADAPTIVE_DM_SYSTEM_SUFFIX
        eff_img = model_manager.get_effective_model_for_function(user_id, "image_generation")
        if str(eff_img.get("model") or "").strip():
            note = (
                image_gen_capability_note
                or (
                    "You can generate images for this user when they clearly ask for a visual (they have an image model configured). "
                    "Do not claim you are text-only or cannot draw/render if they ask for a picture—offer `/imagine` or acknowledge images can be produced in this DM.\n"
                    "If the transcript notes you sent an image earlier, treat that as context only; reference it when relevant. "
                    "Never write bracketed placeholders or pretend to attach images in text—the user only sees real attachments."
                )
            )
            enhanced_system_prompt += "\n\n" + note
    elif is_dm:
        enhanced_system_prompt += (
            "\n\nThis is a direct DM chat. Use a relaxed, natural tone. "
            "Keep it human and concise, and avoid overly formal phrasing."
        )
        eff_img = model_manager.get_effective_model_for_function(user_id, "image_generation")
        if str(eff_img.get("model") or "").strip():
            note = (
                image_gen_capability_note
                or (
                    "This user has an **image generation** model configured. "
                    "Do not claim you are text-only or cannot produce images if they ask for a picture—point them to **`/imagine`**.\n"
                    "If the transcript notes you sent an image earlier, refer to it only when relevant. "
                    "Never output bracketed placeholders for images."
                )
            )
            enhanced_system_prompt += "\n\n" + note
    if fast_reply:
        enhanced_system_prompt += (
            "\n\nFast reply mode is enabled. Be concise and quick: "
            "usually 1-3 short sentences unless the user asks for details."
        )
    
    # Prepare conversation history (channel-based). Persisted turns are user/assistant only;
    # the full system prompt (including current date/time/location) must be injected every request.
    if is_continuation:
        if is_dm:
            try:
                await compact_dm_history_for_channel(user_id, channel_id, username, force=False)
            except Exception:
                pass
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

    rolling = [
        m
        for m in (history or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    if is_dm and len(rolling) > 13:
        # Match prior budget: at most 13 user/assistant turns before the new user message.
        rolling = rolling[-13:]
    messages: List[Dict[str, Any]] = [{"role": "system", "content": enhanced_system_prompt}]
    messages.extend(rolling)

    if is_dm:
        dm_summary = conversation_manager.get_dm_summary_text(channel_id)
        if dm_summary:
            messages.insert(
                1,
                {
                    "role": "system",
                    "content": (
                        "Older DM context (topic notes; may be stale—use only when clearly relevant; "
                        "do not bring up unrelated past topics):\n"
                        f"{dm_summary}"
                    ),
                },
            )
    
    # If we have images, always attach them so fallback can use a vision model
    if images_data:
        user_message = {
            "role": "user",
            "content": formatted_message,
            "images": [img["data"] for img in images_data]
        }
        messages.append(user_message)
    else:
        messages.append({"role": "user", "content": formatted_message})
    
    request_options = None
    if fast_reply:
        request_options = {"num_predict": 220, "temperature": 0.55}

    # Try models with fallback
    final_model, response_text = await _try_models_with_fallback(
        requested_model,
        messages,
        images=bool(images_data),
        provider=provider,
        request_options=request_options,
        abort_check=abort_check,
    )
    if abort_check and await abort_check():
        return ""

    # Clean response
    response_text = _clean_response(response_text)
    if adaptive_dm and str(model_manager.get_effective_model_for_function(user_id, "image_generation").get("model") or "").strip():
        response_text = _strip_leaked_image_placeholders(response_text)
    
    # Store conversation if successful (channel-based; user identity is in formatted_message)
    if (
        persist
        and response_text
        and not response_text.startswith("Error:")
    ):
        skip_adaptive_store = bool(
            adaptive_dm
            and not (reply_context_block or "").strip()
            and is_news_style_dm_bot_text(persisted_user_message)
        )
        if not skip_adaptive_store:
            conversation_manager.add_message(channel_id, "user", persisted_user_message)
            conversation_manager.add_message(channel_id, "assistant", response_text)
            conversation_manager.save()
            if adaptive_dm:
                schedule_dm_adaptive_background_tasks(user_id, channel_id)

    return response_text

def _json_object_end_index(s: str, start: int) -> int:
    """Index after the closing `}` of a JSON object starting at s[start] == '{', or -1 if incomplete."""
    depth = 0
    in_str = False
    esc = False
    for idx in range(start, len(s)):
        c = s[idx]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return idx + 1
    return -1


def _strip_leaked_agent_tool_artifacts(text: str) -> str:
    """
    Remove ReAct-style tool call text or fenced JSON tool payloads that some chat
    models emit before prose (e.g. Action: dalle.text2im + Action Input: {...}).
    """
    if not (text or "").strip():
        return text or ""

    def _fenced_json_looks_like_tool(inner: str) -> bool:
        s = (inner or "").strip().lower()
        if not s.startswith("{"):
            return False
        if "text2im" in s or "dalle." in s or "image_generation" in s:
            return True
        if '"action"' in s or "'action'" in s:
            if "action_input" in s or "action input" in s:
                return True
        return False

    t = str(text)
    # Remove from the end so earlier match positions stay valid.
    for m in reversed(list(re.finditer(r"```\s*json\s*[\r\n]+([\s\S]*?)```", t, flags=re.IGNORECASE))):
        if _fenced_json_looks_like_tool(m.group(1)):
            t = t[: m.start()] + t[m.end() :]

    lines = t.splitlines()
    out_lines: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            re.match(r"^\s*Thought:\s*", line, flags=re.IGNORECASE)
            and i + 1 < len(lines)
            and re.match(r"^\s*Action:\s*\S", lines[i + 1], flags=re.IGNORECASE)
        ):
            i += 1
            continue
        m_action = re.match(r"^\s*Action:\s*\S", line, flags=re.IGNORECASE)
        if m_action:
            i += 1
            if i < len(lines) and re.match(r"^\s*Action Input:\s*", lines[i], flags=re.IGNORECASE):
                buf = re.sub(r"^\s*Action Input:\s*", "", lines[i], flags=re.IGNORECASE).lstrip()
                i += 1
                jb = buf.find("{")
                if jb != -1:
                    end = _json_object_end_index(buf, jb)
                    while end == -1 and i < len(lines):
                        buf = buf + "\n" + lines[i]
                        i += 1
                        end = _json_object_end_index(buf, jb)
                    if end != -1:
                        tail = buf[end:].strip()
                        if tail:
                            out_lines.append(tail)
                continue
            continue
        out_lines.append(line)
        i += 1

    t = "\n".join(out_lines)
    t = re.sub(r"(?im)^\s*Observation:\s*.*(?:\n|$)", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _strip_leaked_image_placeholders(text: str) -> str:
    """Remove fake image-attachment text some models emit; never show to users."""
    if not (text or "").strip():
        return text or ""
    t = str(text)
    t = re.sub(r"\[Sent a generated image:\s*[^\]]*\]", "", t, flags=re.IGNORECASE | re.DOTALL)
    t = _strip_leaked_agent_tool_artifacts(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _clean_response(text):
    """Clean up LLM response"""
    if not text:
        return ""
    
    # Remove unnecessary prefixes
    text = re.sub(r'^assistant:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^ai:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^bot:\s*', '', text, flags=re.IGNORECASE)
    # Don't parrot "X says:" or "X is asking" at start of reply
    text = re.sub(r'^[\w.]+\s+(?:says|is asking):\s*', '', text, flags=re.IGNORECASE)
    
    # Clean up extra whitespace
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    
    return text.strip()


async def ask_llm_shitpost(user_id: int, word: str) -> str:
    """One-shot LLM reply for shitpost: max 2 words confirming/continuing the word. Returns empty on error."""
    system_prompt = get_enhanced_prompt("shitpost")
    eff = model_manager.get_effective_model_for_function(user_id, "shitpost")
    requested_model = eff.get("model") or model_manager.get_last_local_model(user_id, refresh_local=True)
    provider = eff.get("provider", "local")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": word},
    ]
    try:
        _, response_text = await _try_models_with_fallback(
            requested_model,
            messages,
            images=False,
            provider=provider,
        )
        cleaned = _clean_response(response_text or "")
        if cleaned.startswith("Error:"):
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

async def _try_models_with_fallback(
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


async def merge_adaptive_manual_guidance_into_profile(
    user_id: int,
    *,
    current_profile: Dict[str, Any],
    manual_guidance: str,
) -> Tuple[bool, Dict[str, Any], str]:
    """
    Use the command_planner model to fold manual guidance into the structured adaptive profile.
    Does not persist. Returns (ok, new_profile_dict, error_message).
    """
    eff = model_manager.get_effective_model_for_function(user_id, "command_planner")
    requested_model = eff.get("model", "qwen2.5:7b")
    provider = eff.get("provider", "local")
    base_profile = dict(current_profile) if isinstance(current_profile, dict) else {}
    guidance = (manual_guidance or "")[:8000]

    sys_v1 = (
        "You update an adaptive Discord DM user profile as JSON.\n"
        "Input: current_profile (object) and manual_guidance (string).\n"
        "Output: ONE JSON object only — no markdown, no code fences, no commentary before or after.\n"
        "Keys exactly: preferred_name (string), likes (array of strings), dislikes (array of strings), "
        "tone_notes (array of strings).\n"
        "Fold the guidance into those fields: short bullets, distill meaning, do not paste the whole guidance as one string.\n"
        "Keep existing facts unless guidance contradicts them. Max 24 likes, 24 dislikes, 30 tone_notes."
    )
    sys_v2 = (
        "Return ONLY valid minified JSON on a single line. Keys: preferred_name, likes, dislikes, tone_notes. "
        "Arrays of strings. Merge manual_guidance into current_profile."
    )
    user_payload = json.dumps(
        {"current_profile": base_profile, "manual_guidance": guidance},
        ensure_ascii=False,
    )
    last_raw = ""
    for attempt, (sys_prompt, temp, n_pred) in enumerate(
        (
            (sys_v1, 0.25, 900),
            (sys_v1, 0.15, 900),
            (sys_v2, 0.1, 1024),
        )
    ):
        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_payload}]
        try:
            _, raw = await _try_models_with_fallback(
                requested_model,
                messages,
                images=False,
                provider=provider,
                request_options={"num_predict": n_pred, "temperature": temp},
            )
            last_raw = raw or ""
            parsed = _extract_json_object_loose(last_raw)
            if parsed:
                out = _normalize_merge_profile_dict(parsed, base_profile)
                return True, out, ""
        except Exception as e:
            if attempt == 2:
                break
            last_raw = str(e)[:200]

    if guidance.strip():
        return True, _heuristic_merge_manual_into_profile(base_profile, guidance), ""
    return False, dict(base_profile), "merge_parse_failed"


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
        ok, msg = await probe_openrouter_image_model(str(model_name).strip())
        if ok:
            model_manager.set_function_model(user_id, "image_generation", str(model_name).strip(), provider="cloud")
            return True, f"Saved image model: `{model_name}` (cloud)."
        return False, msg
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


async def analyze_file(
    user_id: int,
    channel_id: int,
    filename: str,
    file_data: bytes,
    user_prompt: str = "",
    username: str = "",
    vision_mode: str = "concise",
    return_only_text: bool = False,
    *,
    record_in_conversation: bool = True,
) -> str:
    """vision_mode: concise (short), examine (detailed), interrogate (very short). return_only_text: if True, return only extracted/response text (no header)."""
    if is_adaptive_context_export_filename(filename):
        return (
            "That file is reserved for **`/adaptive-status`** replies in DMs. "
            "Reply to the status message with **`adaptive-dm-context.txt`** attached to replace your adaptive context — "
            "do not use **`/analyze`** for it."
        )
    date, time = update_system_time_date()
    
    # Get persona and model (non-adaptive paths; file analysis uses per-function prefs)
    persona_key = get_function_persona_name("file_analysis")
    system_prompt = persona_manager.get_persona(persona_key)
    eff = model_manager.get_effective_model_for_function(user_id, "file_analysis")
    model_name = eff.get("model", "llama3.2:3b")
    provider = eff.get("provider", "local")
    
    # Determine file type
    file_type = FileProcessor.get_file_type(filename)
    
    # If user prompt is about OCR or text extraction, adjust prompt
    user_prompt_lower = user_prompt.lower()
    is_ocr_request = any(keyword in user_prompt_lower for keyword in 
                        ["ocr", "extract text", "read text", "what does it say", "what's written", "transcribe"])
    
    # Generate appropriate prompt
    if user_prompt:
        analysis_prompt = user_prompt
    else:
        if file_type == "image":
            if is_ocr_request:
                analysis_prompt = "Extract ALL text from this image. Be thorough and accurate. Format the text clearly, preserving line breaks and structure when possible. If there's no text, say 'No text found in the image.'"
            else:
                analysis_prompt = "Describe this image in detail. Be objective and factual. Describe what you actually see, not assumptions. Include: objects, people, animals, text, colors, setting, actions if any. If it's a screenshot or contains text, also extract and transcribe the text."
        else:
            analysis_prompt = FileProcessor.default_analysis_prompt(filename, file_type)
    
    # Prepare image if needed
    images_data = []
    if file_type == "image":
        try:
            img_data = FileProcessor.prepare_image_for_llm(file_data, filename)
            images_data.append(img_data)
        except Exception as e:
            return f"⚠️ Failed to process image: {str(e)}"
    
    if file_type == "image":
        key = {"concise": "file_analysis_image_concise", "examine": "file_analysis_image_examine", "interrogate": "file_analysis_image_interrogate"}.get(vision_mode, "file_analysis_image")
        img_prompt = get_enhanced_prompt(key)
        enhanced_system_prompt = f"{system_prompt}\n\n{img_prompt}"
    else:
        enhanced_system_prompt = f"{system_prompt}\n\n{get_enhanced_prompt('file_analysis_other', date=date, time=time, filename=filename, file_type=file_type)}"
    
    # Prepare messages
    messages = [
        {"role": "system", "content": enhanced_system_prompt},
    ]
    
    # Add image data if present
    if images_data:
        user_message = {
            "role": "user",
            "content": analysis_prompt,
            "images": [img["data"] for img in images_data]
        }
        messages.append(user_message)
    else:
        # For non-image files, include content
        if file_type in ["text", "code", "document"]:
            try:
                text_content = FileProcessor.extract_text(filename, file_data)
                # Limit content length to avoid token limits
                if len(text_content) > 8000:
                    text_content = text_content[:8000] + "\n\n...[Content truncated due to length]..."
                full_prompt = f"{analysis_prompt}\n\nFile Content:\n{text_content}"
                messages.append({"role": "user", "content": full_prompt})
            except Exception as e:
                messages.append({"role": "user", "content": f"{analysis_prompt}\n\nError reading file: {str(e)}"})
        else:
            messages.append({"role": "user", "content": analysis_prompt})
    
    # Get response - use same vision fallback as ask_llm (user's model + fallbacks)
    if file_type == "image":
        model_used, response = await _try_models_with_fallback(model_name, messages, images=True, provider=provider)
        if response and response.startswith("⚠️"):
            pass  # keep warning message
        else:
            model_name = model_used
    else:
        response = await providers.chat(provider, model_name, messages)
        if response is None:
            response = ""
    
    # Clean and format response
    response = _clean_response(response or "")
    
    if record_in_conversation:
        conversation_manager.add_message(
            channel_id,
            "user",
            f"{username} says: [File Upload: {filename}] {user_prompt or 'Analyze this file'}",
        )
        conversation_manager.add_message(channel_id, "assistant", response)
        conversation_manager.save()

    if return_only_text:
        return response
    final_response = f"📄 **File Analysis: {filename}**\n"
    final_response += f"*Type: {file_type.upper()} | Size: {len(file_data):,} bytes*\n\n"
    final_response += response
    return final_response

async def compare_files(
    user_id: int,
    channel_id: int,
    files: List[Dict],
    user_prompt: str = "",
    username: str = "",
    *,
    record_in_conversation: bool = True,
) -> str:
    """Compare multiple text files"""
    # Get system info
    date, time = update_system_time_date()
    
    persona_key = get_function_persona_name("compare_files")
    system_prompt = persona_manager.get_persona(persona_key)
    eff = model_manager.get_effective_model_for_function(user_id, "compare_files")
    model_name = eff.get("model", "llama3.2:3b")
    provider = eff.get("provider", "local")
    
    # Extract text from all files
    file_contents = []
    for file_info in files:
        try:
            text_content = FileProcessor.extract_text(file_info['filename'], file_info['data'])
            # Limit content length
            if len(text_content) > 4000:
                text_content = text_content[:4000] + "\n...[Content truncated]"
            file_contents.append({
                'filename': file_info['filename'],
                'content': text_content,
                'type': FileProcessor.get_file_type(file_info['filename'])
            })
        except Exception as e:
            file_contents.append({
                'filename': file_info['filename'],
                'content': f"Error reading file: {str(e)}",
                'type': 'error'
            })
    
    # Prepare comparison prompt
    if user_prompt:
        analysis_prompt = user_prompt
    else:
        analysis_prompt = "Compare these files. Identify similarities, differences, and provide an overall analysis."
    
    # Build file content for prompt
    file_content_str = ""
    for i, file_info in enumerate(file_contents, 1):
        file_content_str += f"\n\n--- File {i}: {file_info['filename']} ---\n"
        file_content_str += file_info['content']
    
    enhanced_system_prompt = f"{system_prompt}\n\n{get_enhanced_prompt('compare_files', date=date, time=time)}"
    
    messages = [
        {"role": "system", "content": enhanced_system_prompt},
        {"role": "user", "content": f"{analysis_prompt}\n\nFiles to compare:{file_content_str}"}
    ]
    
    response = await providers.chat(provider, model_name, messages)
    response = _clean_response(response or "")
    
    # Format response
    header = f"🔍 **File Comparison: {len(files)} files**\n"
    for file_info in files:
        header += f"- {file_info['filename']} ({len(file_info['data']):,} bytes)\n"
    
    final_response = header + "\n" + response
    
    if record_in_conversation:
        file_names = ", ".join([f["filename"] for f in files])
        conversation_manager.add_message(
            channel_id,
            "user",
            f"{username} says: [File Comparison: {file_names}] {user_prompt or 'Compare these files'}",
        )
        conversation_manager.add_message(channel_id, "assistant", response)
        conversation_manager.save()

    return final_response
