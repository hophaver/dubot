"""DM memory: rolling transcript compaction into topic summaries, brief LLM profile, adaptive profile merges."""
import json
import re
import time
from typing import Any, Dict, List, Tuple

from adaptive_dm import adaptive_dm_manager
from conversations import (
    conversation_manager,
    is_news_style_dm_bot_text,
    is_slash_command_bot_turn,
    strip_discord_recent_context_block,
)
from utils import dm_background
from utils.llm.fallback import try_models_with_fallback
from utils.llm.prompts import function_model, function_system, get_enhanced_prompt
from utils.llm.providers import is_error_reply
from utils.llm.text import clean_response, extract_json_object_loose

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
    requested_model, provider = function_model(user_id, "dm_summary")
    sys_p = get_enhanced_prompt("dm_summary_topics")
    full_sys = function_system("dm_summary", sys_p)
    user_block = (
        f"existing_topics_json:\n{json.dumps(existing[-14:], ensure_ascii=False)}\n\n"
        f"older_transcript:\n{joined_old_transcript[:12000]}"
    )
    messages = [{"role": "system", "content": full_sys}, {"role": "user", "content": user_block}]
    try:
        _, raw = await try_models_with_fallback(
            requested_model,
            messages,
            images=False,
            provider=provider,
            request_options={"num_predict": 420, "temperature": 0.2},
        )
    except Exception:
        return
    parsed = extract_json_object_loose(raw or "")
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
    requested_model, provider = function_model(user_id, "dm_summary")
    sys_p = get_enhanced_prompt("dm_user_profile_brief")
    full_sys = function_system("dm_summary", sys_p)
    payload = "previous_profile:\n" + (prev or "(none)") + "\n\nrecent_user_lines:\n" + "\n---\n".join(user_bits[-8:])
    try:
        _, raw = await try_models_with_fallback(
            requested_model,
            [{"role": "system", "content": full_sys}, {"role": "user", "content": payload[:8000]}],
            images=False,
            provider=provider,
            request_options={"num_predict": 200, "temperature": 0.15},
        )
    except Exception:
        return
    out = clean_response(raw or "").strip()
    if not out or is_error_reply(out):
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
    requested_model, provider = function_model(user_id, "command_planner")
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
            _, raw = await try_models_with_fallback(
                requested_model,
                messages,
                images=False,
                provider=provider,
                request_options={"num_predict": n_pred, "temperature": temp},
            )
            last_raw = raw or ""
            parsed = extract_json_object_loose(last_raw)
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
