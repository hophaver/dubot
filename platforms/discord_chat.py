import discord
import subprocess
import sys
import os
import io
import mimetypes
import asyncio
import inspect
import shlex
import re
from typing import Optional, Any, Dict, List, Tuple, get_args, get_origin
from discord import app_commands
from discord.ui import View, Button
from config import get_config, get_wake_word, set_bot_awake
from conversations import conversation_manager
from services.reminder_service import reminder_manager
from models import model_manager
from utils.llm_service import (
    ask_llm,
    plan_command_from_text,
    analyze_file,
    compare_files,
    build_adaptive_command_schema,
    compact_dm_history_for_channel,
    schedule_dm_adaptive_background_tasks,
    _strip_leaked_image_placeholders,
    merge_adaptive_manual_guidance_into_profile,
)
from commands.translate.translate import do_translate
from utils.adaptive_dm_image_pipeline import run_adaptive_dm_image_file_pipeline
from utils.dm_image_flow_temp import FINAL_NAME, read_text, remove_session_dir
from utils.ha_integration import ask_home_assistant
from utils import home_log
from utils import reliability_telemetry
from integrations import PERMANENT_ADMIN
from adaptive_dm import ADAPTIVE_DM_SYSTEM_SUFFIX, adaptive_dm_manager, is_adaptive_context_export_filename
from commands.shared import sanitize_discord_bot_content, _CHUNK_SEND_DELAY, _chunk_message, MAX_MESSAGE_LENGTH


def _is_transient_http_error(exc: Exception) -> bool:
    if not isinstance(exc, discord.HTTPException):
        return False
    status = getattr(exc, "status", None)
    return status in {408, 425, 429, 500, 502, 503, 504}


async def _send_with_retry(send_coro_factory, retries: int = 3):
    last_error = None
    for attempt in range(retries):
        try:
            return await send_coro_factory()
        except Exception as exc:
            last_error = exc
            if _is_transient_http_error(exc) and attempt < retries - 1:
                retry_count = reliability_telemetry.increment("discord_send_retries")
                await home_log.send_to_home(
                    f"⚠️ Discord send retry ({attempt + 1}/{retries - 1}) due to transient HTTP error. "
                    f"retry_count={retry_count}"
                )
                await asyncio.sleep(1 + attempt)
                continue
            error_count = reliability_telemetry.increment("discord_send_errors")
            await home_log.send_to_home(
                f"🔴 Discord send failed after retries (error #{error_count}): {str(exc)[:280]}. "
                f"{reliability_telemetry.format_snapshot('Counters')}"
            )
            raise
    if last_error:
        raise last_error
    return None


def _schedule_adaptive_post_reply_calibration(message: discord.Message, message_text: str):
    """Queue user-only samples and periodically update adaptive DM tone after replying."""
    user_id = getattr(message.author, "id", None)
    if user_id is None:
        return
    if not message_text:
        return
    if not adaptive_dm_manager.is_enabled(user_id):
        return

    async def _runner():
        try:
            await asyncio.to_thread(adaptive_dm_manager.apply_live_message_tune, user_id, message_text)
            await asyncio.to_thread(adaptive_dm_manager.run_tone_tuning_now, user_id, False)
        except Exception:
            pass

    asyncio.create_task(_runner())


async def _send_chat_output(message: discord.Message, content=None, *, embed=None, embeds=None, file=None, files=None, view=None):
    """Send naturally in DMs; reply in non-DM channels."""
    if isinstance(message.channel, discord.DMChannel):
        return await message.channel.send(content=content, embed=embed, embeds=embeds, file=file, files=files, view=view)
    return await message.reply(content=content, embed=embed, embeds=embeds, file=file, files=files, view=view)


async def _execute_planned_command(
    client: discord.Client,
    message: discord.Message,
    command_name: str,
    args: dict,
    *,
    not_found_message: str,
    failure_message: str,
) -> bool:
    """Resolve and execute a planned slash command using message-proxy interaction."""
    command_name = str(command_name or "").strip().lower()
    command_obj = client.tree.get_command(command_name)
    if command_obj is None:
        await _send_chat_output(message, not_found_message)
        return True
    try:
        kwargs = _build_kwargs_from_plan(client, message, command_obj, args or {})
        interaction = _MessageInteractionProxy(client, message, command_obj.name)
        await command_obj.callback(interaction, **kwargs)
    except Exception as exc:
        await _send_chat_output(message, f"{failure_message} `/{command_obj.name}`: {str(exc)[:200]}")
    return True


async def _read_message_attachments(message: discord.Message):
    attachments = []
    for att in list(getattr(message, "attachments", []) or []):
        if is_adaptive_context_export_filename(getattr(att, "filename", None)):
            continue
        try:
            data = await att.read()
            attachments.append({"filename": att.filename, "data": data})
        except Exception as exc:
            reliability_telemetry.increment("discord_send_errors")
            await home_log.send_to_home(
                f"⚠️ Failed to read Discord attachment `{getattr(att, 'filename', 'unknown')}` "
                f"in channel {getattr(message.channel, 'id', 'unknown')}: {str(exc)[:220]}"
            )
    return attachments


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in {"true", "1", "yes", "on", "enable", "enabled"}:
        return True
    if v in {"false", "0", "no", "off", "disable", "disabled"}:
        return False
    raise ValueError("must be true/false")


def _split_admin_tokens(text: str):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _coerce_channel(guild: Optional[discord.Guild], raw: str):
    if guild is None:
        raise ValueError("must be used in a server")
    m = re.match(r"<#(\d+)>", raw.strip())
    channel_id = int(m.group(1)) if m else int(raw.strip())
    channel = guild.get_channel(channel_id)
    if channel is None:
        raise ValueError("channel not found")
    return channel


def _coerce_user(client: discord.Client, guild: Optional[discord.Guild], raw: str):
    text = raw.strip()
    m = re.match(r"<@!?(\d+)>", text)
    user_id = int(m.group(1)) if m else int(text)
    if guild is not None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
    return client.get_user(user_id) or discord.Object(id=user_id)


def _annotation_is_choice(annotation: Any) -> bool:
    text = str(annotation)
    if "Choice" in text:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return any("Choice" in str(a) for a in get_args(annotation))


def _parameter_usage(command_name: str, param) -> str:
    pname = getattr(param, "display_name", None) or param.name
    ptype = str(getattr(param, "type", "string")).split(".")[-1]
    choices = getattr(param, "choices", None) or []
    if choices:
        choice_vals = "/".join(str(c.value) for c in choices)
        hint = choice_vals
    elif ptype == "boolean":
        hint = "true|false"
    elif ptype == "integer":
        hint = "number"
    elif ptype == "number":
        hint = "decimal"
    elif ptype == "attachment":
        hint = "attachment"
    elif ptype == "channel":
        hint = "#channel|channel_id"
    elif ptype == "user":
        hint = "@user|user_id"
    else:
        hint = "text"
    if getattr(param, "required", False):
        return f"{pname}:<{hint}>"
    return f"[{pname}:<{hint}>]"


def _build_command_usage(command_obj) -> str:
    parts = [_parameter_usage(command_obj.name, p) for p in command_obj.parameters]
    suffix = (" " + " ".join(parts)) if parts else ""
    return f"!{command_obj.name}{suffix}"


def _is_positive_confirmation(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in {"yes", "y", "confirm", "do it", "run it", "ok", "okay", "proceed", "sure"}:
        return True
    return bool(re.match(r"^(yes|yep|yeah|ok|okay|sure)\b", t))


def _is_negative_confirmation(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in {"no", "n", "cancel", "stop", "don't", "dont", "never mind", "nevermind"}:
        return True
    return bool(re.match(r"^(no|nah|cancel|stop)\b", t))


def _looks_like_download_request(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    if "http://" in t or "https://" in t:
        return any(k in t for k in ["download", "save", "grab", "fetch", "media", "video", "audio", "image", "file"])
    return any(
        phrase in t
        for phrase in [
            "download this",
            "download that",
            "save this",
            "grab this link",
            "get this video",
            "get this file",
        ]
    )


def _ext_for_generated_image_mime(mime: str) -> str:
    m = (mime or "image/png").split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(m) or ".png"
    if ext == ".jpe":
        ext = ".jpg"
    return ext


def _adaptive_dm_explicit_image_intent(text: str) -> bool:
    """
    Natural-language image generation in adaptive DMs when the user clearly wants a visual.
    Slash /imagine is handled by the adaptive command planner, not here.
    """
    raw = (text or "").strip()
    if not raw or raw.startswith("/"):
        return False
    t = raw.lower()
    if t.startswith("imagine "):
        rest = raw[len("imagine ") :].strip()
        if rest.lower().startswith("if "):
            return False
        return bool(rest)
    patterns = (
        r"\b(generate|create|make|draw|render)\s+(?:me\s+)?(?:an?\s+)?(image|picture|photo|diagram|illustration|mockup|mock-up)\b",
        r"\b(show|give)\s+me\s+(?:an?\s+)?(image|picture|photo|diagram)\b",
        r"\bshow\s+me\s+what\b",
        r"\bwhat\s+(?:it|this|that|the\s+\w+)\s+could\s+look\s+like\b",
        r"\bwhat\s+would\s+(?:it|this|that)\s+look\s+like\b",
        r"\bwhat\s+would\s+.+\s+look\s+like\b",
        r"\bvisualize\b",
        r"\bvisualise\b",
        r"\bsketch\b",
        r"\bimage\s+of\b",
        r"\bpicture\s+of\b",
        r"\bdiagram\s+of\b",
        r"\bwireframe\b",
        r"\bvisual\s+reference\b",
        r"\breference\s+(?:image|picture|render)\b",
    )
    if any(re.search(p, t, flags=re.IGNORECASE) for p in patterns):
        return True
    # "show me" + look/visual/finished device etc. (avoid bare "show me the code")
    if re.search(r"\bshow\s+me\b", t) and re.search(
        r"\b(look|looks|looking|visual|render|layout|device|finished|prototype|mockup|design)\b",
        t,
        flags=re.IGNORECASE,
    ):
        return True
    return False


async def _build_wake_message_reply_context(
    client: discord.Client, message: discord.Message
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    When the user replies to any message and activates the bot with wake word / mention,
    include the referenced message (text and/or image) plus the activating line.
    Returns (context_block, extra_attachments_for_llm).
    """
    ref = message.reference
    if not ref or not ref.message_id:
        return None, []
    try:
        ref_msg = ref.resolved
        if ref_msg is None:
            ref_msg = await message.channel.fetch_message(ref.message_id)
    except Exception:
        return None, []
    if ref_msg is None:
        return None, []
    author = getattr(ref_msg.author, "display_name", None) or getattr(ref_msg.author, "name", "user")
    body = (ref_msg.content or "").strip()
    lines = ["[User is replying to this earlier message — use it as primary context]"]
    lines.append(f"Referenced message from {author}: {body or '(no text)'}")
    imgs = [a for a in (ref_msg.attachments or []) if (a.filename or "").lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))]
    extra_attachments = []
    for att in imgs[:3]:
        try:
            data = await att.read()
            extra_attachments.append({"filename": att.filename, "data": data})
        except Exception:
            pass
    wake = (get_config().get("wake_word", "robot") or "robot").strip()
    trigger = (message.content or "").strip()
    if wake and trigger.lower().startswith(wake.lower()):
        trigger = trigger[len(wake) :].strip()
    lines.append(f"User's new message (after wake word): {trigger}")
    block = "\n".join(lines)
    return block, extra_attachments


async def _try_send_adaptive_dm_imagine(client: discord.Client, message: discord.Message, clean_content: str) -> bool:
    """Return True when handled (including errors)."""
    uid = message.author.id
    eff = model_manager.get_effective_model_for_function(uid, "image_generation")
    model_name = str(eff.get("model") or "").strip()
    if not model_name:
        await _send_chat_output(
            message,
            "Image generation is not set up yet — an admin needs to add an OpenRouter image model "
            "(`**/pull-model**` → type **image generation (OpenRouter)**), then pick it in **`/llm-settings`** → **Image generation**.",
        )
        return True

    idea = (clean_content or "").strip()
    if idea.lower().startswith("imagine "):
        idea = idea[8:].strip()
    if not idea:
        await _send_chat_output(message, "Say what to generate (e.g. **imagine** a red panda in a spacesuit), or use **`/imagine`**.")
        return True

    session_dir = None
    try:
        try:
            session_dir, img_bytes, mime, err = await run_adaptive_dm_image_file_pipeline(
                uid,
                message.channel.id,
                str(message.author.name),
                idea,
                model_name,
            )
            if err:
                await _send_chat_output(message, f"❌ {err}")
                return True
            if not img_bytes:
                await _send_chat_output(
                    message,
                    "❌ No image came back from the model. Try another model or a clearer prompt.",
                )
                return True

            final_path = session_dir / FINAL_NAME
            content = _strip_leaked_image_placeholders((await read_text(final_path)).strip())
            if not content:
                content = "Here’s the image."
            if len(content) > 1900:
                content = content[:1890].rstrip() + "…"

            ext = _ext_for_generated_image_mime(mime)
            file = discord.File(io.BytesIO(img_bytes), filename=f"imagine{ext}")

            sent = await _send_with_retry(lambda: _send_chat_output(message, content=content, file=file))
            try:
                cid = message.channel.id
                conversation_manager.add_message(cid, "user", f"{message.author.name} says: {clean_content}")
                conversation_manager.add_message(cid, "assistant", content)
            except Exception:
                pass
            conversation_manager.set_last_bot_message(message.channel.id, sent.id)
            conversation_manager.save()
            _schedule_adaptive_post_reply_calibration(message, clean_content)
            return True
        except Exception as exc:
            await home_log.send_to_home(
                f"🔴 adaptive_dm_image_pipeline failed user={uid} ch={message.channel.id}: {str(exc)[:400]}"
            )
            await _send_chat_output(
                message,
                "⚠️ Something went wrong while generating that image. Please try again.",
            )
            return True
    finally:
        await remove_session_dir(session_dir)


def _looks_like_command_request(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if t.startswith("/"):
        return True
    cue_words = [
        "run ",
        "execute ",
        "set ",
        "change ",
        "switch ",
        "download ",
        "remind ",
        "restart",
        "kill ",
        "update ",
        "status",
        "help",
        "list ",
        "balance",
        "credit",
        "openrouter",
        "/bal",
    ]
    return any(cue in t for cue in cue_words)


def _looks_like_himas_request(text: str) -> bool:
    """
    Detect likely smart-home commands for chat-triggered /himas.
    Kept strict on purpose: words like \"set\", \"switch\", and \"status\" appear constantly in normal chat.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    tl = raw.lower()
    if tl.startswith("/himas") or tl.startswith("himas "):
        return True

    trimmed = re.sub(
        r"^(can you|could you|would you|please|hey|yo|hi|hello)[,\s]+",
        "",
        tl,
        flags=re.IGNORECASE,
    ).strip()
    # Short utterances only; allow extra headroom for chained "… and …" HA commands.
    max_scan = min(len(trimmed), 300)
    hay = trimmed[:max_scan]

    patterns = (
        r"\bturn\s+on\b",
        r"\bturn\s+off\b",
        r"\bswitch\s+on\b",
        r"\bswitch\s+off\b",
        r"\blights?\s+on\b",
        r"\blights?\s+off\b",
        r"\bturn\s+(?:the\s+|my\s+)[\w\s]{1,42}?\s+on\b",
        r"\bturn\s+(?:the\s+|my\s+)[\w\s]{1,42}?\s+off\b",
        r"\bswitch\s+(?:the\s+|my\s+)[\w\s]{1,42}?\s+on\b",
        r"\bswitch\s+(?:the\s+|my\s+)[\w\s]{1,42}?\s+off\b",
        r"\btoggle\s+(?:the|my|all)\s+\w",  # e.g. toggle the lights (not bare "toggle my …" in chat)
        r"\btoggle\s+(?:the\s+|my\s+)?(?:\w+\s+){0,2}(?:lights?|lamps?|fans?)\b",
        r"\bdim\s+(?:the|my)\b",
        # Brightness / thermostat style only (not generic "set … to …")
        r"\bset\s+(?:the\s+)?(?:lights?|lamps?|thermostat|temperature|fan|brightness|ceiling|kitchen|bedroom|living\s+room|heater|hvac|ac)\b[\w\s,'-]{0,40}?(?:to|at)\s+\d{1,3}\s*%",
        # set kitchen red 60% (color + brightness without "to")
        r"\bset\s+(?:the\s+)?[\w\s]{1,32}\s+(?:red|green|blue|white|yellow|orange|purple|pink|cyan|magenta|warm\s+white|cool\s+white)\s+\d{1,3}\s*%",
        r"\bset\s+(?:the\s+)?(?:thermostat|temperature|hvac|heat|cool|ac)\b[\w\s,'-]{0,35}?(?:to|at)\s+\d{1,3}\s*(?:°|degrees?\b)?",
        r"\b(?:what\s*'?\s*s|what\s+is)\s+the\s+temperature\s+(?:in|of)\s+\w",
    )
    return any(re.search(p, hay, flags=re.IGNORECASE) for p in patterns)


def _normalize_command_name(name: str) -> str:
    n = (name or "").strip().lower().replace("/", "")
    if n in {"ha", "home", "homeassistant", "home-assistant"}:
        return "himas"
    return n


_INVALID_PREFERENCE_TOKENS = {
    "anymore", "again", "this", "that", "it", "command", "confirmation",
    "confirm", "yes", "no", "please", "one", "same",
}


def _is_valid_preference_command(name: str) -> bool:
    n = _normalize_command_name(name)
    return bool(n) and n not in _INVALID_PREFERENCE_TOKENS


def _looks_like_disable_confirmation_phrase(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    if "without confirmation" in t or "no confirmation" in t:
        return True
    return bool(
        re.search(
            r"(?:wont|won't|will not|doesnt|doesn't|dont|don't)\s+need\s+(?:a\s+)?confirm(?:ation)?",
            t,
            flags=re.IGNORECASE,
        )
    )


def _normalize_himas_command_text(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(
        r"^(can you|could you|would you|please|hey|yo|hi|hello)\s+",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(r"\s+(please|thanks|thank you)\s*$", "", t, flags=re.IGNORECASE).strip()
    return t or (text or "").strip()


def _quick_command_plan_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Fast deterministic parser for common command intents."""
    raw = (text or "").strip()
    t = raw.lower()
    if not t:
        return None
    # Direct slash/bang command with no args.
    for simple in ("bal", "help", "status", "wake", "sleep", "checkwake"):
        if t == f"/{simple}" or t == simple:
            return {"should_execute": True, "command": simple, "arguments": {}, "reason": "direct command", "risk": "safe"}

    if any(k in t for k in ["openrouter balance", "openrouter credits", "my credits", "check credits", "check balance"]):
        return {"should_execute": True, "command": "bal", "arguments": {}, "reason": "credits query", "risk": "safe"}
    if t in {"balance", "credits", "credit balance"}:
        return {"should_execute": True, "command": "bal", "arguments": {}, "reason": "credits query", "risk": "safe"}

    if t.startswith("/himas "):
        return {
            "should_execute": True,
            "command": "himas",
            "arguments": {"command": _normalize_himas_command_text(t[7:].strip())},
            "reason": "direct home assistant command",
            "risk": "safe",
        }
    if t.startswith("/imagine"):
        rest = raw[len("/imagine") :].strip() if raw else ""
        return {
            "should_execute": True,
            "command": "imagine",
            "arguments": {"idea": rest},
            "reason": "direct /imagine",
            "risk": "safe",
        }
    return None


def _extract_no_confirm_preference(text: str):
    """Return (command_name, enabled) when user asks to toggle confirmations for a command."""
    t = (text or "").strip().lower()
    if not t:
        return None

    disable_patterns = [
        r"(?:no need|dont|don't|stop|skip)\s+(?:for\s+)?(?:the\s+)?confirm(?:ation)?\s+(?:for\s+)?/?([a-z0-9\-]+)",
        r"(?:always|just)\s+run\s+/?([a-z0-9\-]+)\s+(?:without|w/o)\s+confirm(?:ation)?",
        r"/?([a-z0-9\-]+)\s+(?:doesn't|doesnt|does not|shouldn't|shouldnt|should not)\s+need\s+confirm(?:ation)?",
    ]
    enable_patterns = [
        r"(?:ask|require|need)\s+confirm(?:ation)?\s+(?:for\s+)?/?([a-z0-9\-]+)",
        r"(?:enable|turn on)\s+confirm(?:ation)?\s+(?:for\s+)?/?([a-z0-9\-]+)",
    ]

    for pat in disable_patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            cmd = _normalize_command_name(m.group(1))
            if _is_valid_preference_command(cmd):
                return cmd, True
            return "__PENDING__", True

    for pat in enable_patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            cmd = _normalize_command_name(m.group(1))
            if _is_valid_preference_command(cmd):
                return cmd, False
            return "__PENDING__", False

    if "this command" in t and ("without confirmation" in t or "no confirmation" in t):
        return "__PENDING__", True
    if "this" in t and _looks_like_disable_confirmation_phrase(t):
        return "__PENDING__", True
    if "this command" in t and ("ask confirmation" in t or "with confirmation" in t):
        return "__PENDING__", False
    return None


def _build_command_schema(client: discord.Client):
    schema = []
    for cmd in client.tree.get_commands():
        params = []
        for p in cmd.parameters:
            params.append(
                {
                    "name": p.name,
                    "required": bool(getattr(p, "required", False)),
                    "type": str(getattr(p, "type", "")).split(".")[-1],
                    "choices": [str(c.value) for c in (getattr(p, "choices", None) or [])],
                }
            )
        schema.append({"name": cmd.name, "description": cmd.description or "", "parameters": params})
    return schema


def _build_kwargs_from_plan(client: discord.Client, message: discord.Message, command_obj, arguments: dict):
    kwargs = {}
    attachments = list(getattr(message, "attachments", []) or [])
    attach_idx = 0
    arguments = arguments or {}
    for p in command_obj.parameters:
        pname = p.name
        raw = arguments.get(pname)
        if raw is None:
            display_name = getattr(p, "display_name", None)
            if display_name:
                raw = arguments.get(display_name)
        ptype = str(getattr(p, "type", "")).split(".")[-1]
        if raw is None and ptype == "attachment" and attach_idx < len(attachments):
            raw = attachments[attach_idx]
            attach_idx += 1
        if raw is None:
            if getattr(p, "required", False):
                raise ValueError(f"missing required argument `{pname}`")
            continue

        if ptype == "integer":
            kwargs[pname] = int(raw)
        elif ptype == "number":
            kwargs[pname] = float(raw)
        elif ptype == "boolean":
            kwargs[pname] = _parse_bool(str(raw))
        elif ptype == "channel":
            kwargs[pname] = _coerce_channel(message.guild, str(raw))
        elif ptype == "user":
            kwargs[pname] = _coerce_user(client, message.guild, str(raw))
        elif ptype == "attachment":
            if not isinstance(raw, discord.Attachment):
                raise ValueError(f"`{pname}` requires an attached file")
            kwargs[pname] = raw
        else:
            kwargs[pname] = str(raw)
    return kwargs


class _MessageResponseProxy:
    def __init__(self, message: discord.Message):
        self.message = message
        self._done = False
        self._original_message: Optional[discord.Message] = None

    def is_done(self):
        return self._done

    async def send_message(self, content=None, *, embed=None, embeds=None, file=None, files=None, view=None, ephemeral=False):
        sent = await self.message.reply(content=content, embed=embed, embeds=embeds, file=file, files=files, view=view)
        self._done = True
        if self._original_message is None:
            self._original_message = sent
        return sent

    async def defer(self, ephemeral=False):
        self._done = True

    async def defer_update(self):
        self._done = True

    async def edit_message(self, *, content=None, embed=None, view=None):
        self._done = True
        target = self._original_message
        if target is not None:
            await target.edit(content=content, embed=embed, view=view)


class _MessageFollowupProxy:
    def __init__(self, response_proxy: _MessageResponseProxy):
        self._response_proxy = response_proxy
        self._message = response_proxy.message

    async def send(self, content=None, *, embed=None, embeds=None, file=None, files=None, view=None, ephemeral=False):
        sent = await self._message.channel.send(content=content, embed=embed, embeds=embeds, file=file, files=files, view=view)
        if self._response_proxy._original_message is None:
            self._response_proxy._original_message = sent
        return sent


class _MessageInteractionProxy:
    def __init__(self, client: discord.Client, message: discord.Message, command_name: str):
        self.client = client
        self.user = message.author
        self.channel = message.channel
        self.guild = message.guild
        self.response = _MessageResponseProxy(message)
        self.followup = _MessageFollowupProxy(self.response)
        self.command = type("ProxyCommand", (), {"name": command_name})()

    async def edit_original_response(self, *, content=None, embed=None, view=None):
        if self.response._original_message is None:
            sent = await self.channel.send(content=content, embed=embed, view=view)
            self.response._original_message = sent
            return sent
        await self.response._original_message.edit(content=content, embed=embed, view=view)
        return self.response._original_message


def _parse_admin_bang_kwargs(client: discord.Client, message: discord.Message, command_obj, arg_text: str):
    params = list(command_obj.parameters)
    callback_sig = inspect.signature(command_obj.callback)
    callback_params = list(callback_sig.parameters.values())[1:]
    callback_annotations = [p.annotation for p in callback_params]

    named = {}
    positional = []
    tokens = _split_admin_tokens(arg_text)
    for token in tokens:
        if ":" in token:
            key, val = token.split(":", 1)
            if key:
                named[key.strip().lower()] = val
                continue
        if "=" in token:
            key, val = token.split("=", 1)
            if key:
                named[key.strip().lower()] = val
                continue
        positional.append(token)

    kwargs = {}
    attachments = list(getattr(message, "attachments", []) or [])
    attach_idx = 0
    pos_idx = 0

    # Natural text convenience for one-string-arg commands.
    if (
        len(params) == 1
        and getattr(params[0], "required", False)
        and str(getattr(params[0], "type", "")).endswith("string")
        and not named
        and arg_text.strip()
    ):
        kwargs[params[0].name] = arg_text.strip()
        return kwargs

    for idx, param in enumerate(params):
        pname = param.name
        pdisp = (getattr(param, "display_name", None) or pname).lower()
        raw = None
        if pname.lower() in named:
            raw = named[pname.lower()]
        elif pdisp in named:
            raw = named[pdisp]
        elif str(getattr(param, "type", "")).endswith("attachment"):
            if attach_idx < len(attachments):
                raw = attachments[attach_idx]
                attach_idx += 1
        elif pos_idx < len(positional):
            raw = positional[pos_idx]
            pos_idx += 1

        if raw is None:
            if getattr(param, "required", False):
                raise ValueError(f"missing required argument `{pname}`")
            continue

        ptype = str(getattr(param, "type", "")).split(".")[-1]
        ann = callback_annotations[idx] if idx < len(callback_annotations) else None
        if _annotation_is_choice(ann):
            kwargs[pname] = app_commands.Choice(name=str(raw), value=str(raw))
        elif ptype == "integer":
            kwargs[pname] = int(raw)
        elif ptype == "number":
            kwargs[pname] = float(raw)
        elif ptype == "boolean":
            kwargs[pname] = _parse_bool(str(raw))
        elif ptype == "channel":
            kwargs[pname] = _coerce_channel(message.guild, str(raw))
        elif ptype == "user":
            kwargs[pname] = _coerce_user(client, message.guild, str(raw))
        elif ptype == "attachment":
            if not isinstance(raw, discord.Attachment):
                raise ValueError(f"`{pname}` requires an attached file")
            kwargs[pname] = raw
        else:
            kwargs[pname] = str(raw)

        choices = getattr(param, "choices", None) or []
        if choices and not _annotation_is_choice(ann):
            allowed = {str(c.value) for c in choices}
            if str(kwargs[pname]) not in allowed:
                raise ValueError(f"`{pname}` must be one of: {', '.join(sorted(allowed))}")

    if pos_idx < len(positional):
        extras = " ".join(positional[pos_idx:])
        raise ValueError(f"unexpected extra arguments: {extras}")
    return kwargs


def _format_discord_admin_bang_help(client: discord.Client) -> str:
    commands = sorted(c.name for c in client.tree.get_commands())
    preview = ", ".join(f"`!{name}`" for name in commands)
    return f"Global admin can use `!` for all slash commands.\nAvailable: {preview}"


async def _process_admin_bang_slash_command(client, message: discord.Message, bang_payload: str) -> bool:
    payload = (bang_payload or "").strip()
    if not payload:
        await _send_chat_output(message, _format_discord_admin_bang_help(client))
        return True

    parts = payload.split(maxsplit=1)
    raw_command = parts[0].lower().replace("_", "-")
    arg_text = parts[1] if len(parts) > 1 else ""
    command_obj = client.tree.get_command(raw_command)
    if command_obj is None:
        await _send_chat_output(
            message,
            f"❌ Unknown command `!{raw_command}`.\nUse `/help` for all commands."
        )
        return True

    try:
        kwargs = _parse_admin_bang_kwargs(client, message, command_obj, arg_text)
    except Exception as exc:
        await _send_chat_output(
            message,
            f"❌ Invalid syntax for `!{command_obj.name}`: {str(exc)}\n"
            f"Format: `{_build_command_usage(command_obj)}`"
        )
        return True

    interaction = _MessageInteractionProxy(client, message, command_obj.name)
    try:
        await command_obj.callback(interaction, **kwargs)
    except TypeError as exc:
        await _send_chat_output(
            message,
            f"❌ Invalid syntax for `!{command_obj.name}`.\n"
            f"Format: `{_build_command_usage(command_obj)}`\n"
            f"Details: {str(exc)[:180]}"
        )
    except Exception as exc:
        await _send_chat_output(
            message,
            f"❌ Command `!{command_obj.name}` failed: {str(exc)[:200]}\n"
            f"Format: `{_build_command_usage(command_obj)}`"
        )
    return True


class AdaptiveManualMergeConfirmView(View):
    """Confirm or revert after /adaptive-status reply (manual merge or full context replace)."""

    def __init__(self, owner_id: int, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only you can use these buttons.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        try:
            adaptive_dm_manager.clear_pending_manual_merge(self.owner_id)
        except Exception:
            pass

    @staticmethod
    def _empty_profile() -> Dict[str, Any]:
        return {"preferred_name": "", "likes": [], "dislikes": [], "tone_notes": []}

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        pending = adaptive_dm_manager.get_pending_manual_merge(self.owner_id)
        if not pending:
            await interaction.response.send_message("Nothing to confirm anymore — run **`/adaptive-status`** again.", ephemeral=True)
            self.stop()
            return
        kind = pending.get("kind") or "manual_merge"
        await interaction.response.defer()
        if kind == "full_replace":
            body_core = str(pending.get("new_body_core", "") or "").strip()
            adaptive_dm_manager.apply_context_file_replace(self.owner_id, body_core, reset_profile=True)
        elif kind == "manual_merge":
            new_prof = pending.get("new_profile")
            if not isinstance(new_prof, dict):
                await interaction.followup.send("Invalid pending state.", ephemeral=True)
                self.stop()
                return
            adaptive_dm_manager.replace_profile_data(self.owner_id, new_prof)
            adaptive_dm_manager.clear_profile_manual_override(self.owner_id)
        else:
            await interaction.followup.send("Invalid pending state.", ephemeral=True)
            self.stop()
            return
        adaptive_dm_manager.clear_pending_manual_merge(self.owner_id)
        try:
            from adaptive_dm import export_adaptive_to_personas
            from personas import persona_manager as _pm

            export_adaptive_to_personas(_pm)
        except Exception:
            pass
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        msg = (
            "✅ Full context applied. The auto-learned block will grow again from your DMs."
            if kind == "full_replace"
            else "✅ Applied. Auto-tuning from your messages continues."
        )
        await interaction.followup.send(msg, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Revert", style=discord.ButtonStyle.danger)
    async def revert(self, interaction: discord.Interaction, button: Button):
        pending = adaptive_dm_manager.get_pending_manual_merge(self.owner_id)
        if not pending:
            await interaction.response.send_message("Nothing to revert.", ephemeral=True)
            self.stop()
            return
        kind = pending.get("kind") or "manual_merge"
        await interaction.response.defer()
        if kind == "full_replace":
            prev = pending.get("previous_profile")
            prev_ov = str(pending.get("previous_override_body", "") or "")
            prev_prefix = str(pending.get("previous_prefix", "") or "")
            prev_legacy = str(pending.get("previous_legacy_manual", "") or "")
            if isinstance(prev, dict):
                adaptive_dm_manager.restore_context_file_replace_state(
                    self.owner_id,
                    profile=prev,
                    override_body=prev_ov,
                    manual_prefix=prev_prefix,
                    legacy_manual=prev_legacy,
                )
            else:
                adaptive_dm_manager.clear_context_override_body(self.owner_id)
        else:
            prev = pending.get("previous_profile")
            if isinstance(prev, dict):
                adaptive_dm_manager.replace_profile_data(self.owner_id, prev)
        adaptive_dm_manager.clear_pending_manual_merge(self.owner_id)
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send("↩️ Reverted.", ephemeral=True)
        self.stop()


async def _read_first_txt_attachment(message: discord.Message) -> Optional[str]:
    for att in list(getattr(message, "attachments", []) or []):
        fn = (att.filename or "").lower()
        if not fn.endswith(".txt"):
            continue
        try:
            raw = await att.read()
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    return None


async def _read_adaptive_context_txt_attachment(message: discord.Message) -> Tuple[Optional[str], Optional[str]]:
    """Return (filename, text) for exact adaptive-dm-context.txt attachment."""
    for att in list(getattr(message, "attachments", []) or []):
        fn = (att.filename or "").strip()
        if not is_adaptive_context_export_filename(fn):
            continue
        try:
            raw = await att.read()
            return fn, raw.decode("utf-8", errors="replace")
        except Exception:
            return fn, None
    return None, None


async def _try_handle_dm_status_reply(client: discord.Client, message: discord.Message) -> bool:
    """Merge manual notes into adaptive auto-learned profile; optional full-export paste still supported."""
    if not isinstance(message.channel, discord.DMChannel):
        return False
    ref = message.reference
    if not ref or not ref.message_id:
        return False
    uid = message.author.id
    anchor = adaptive_dm_manager.get_status_reply_anchor(uid)
    if not anchor:
        return False
    if message.channel.id != anchor["channel_id"] or ref.message_id != anchor["message_id"]:
        return False
    try:
        ref_msg = ref.resolved
        if ref_msg is None:
            ref_msg = await message.channel.fetch_message(ref.message_id)
        if ref_msg.author.id != client.user.id:
            return False
    except Exception:
        return False

    text = (message.content or "").strip()
    ctx_fn, ctx_body = await _read_adaptive_context_txt_attachment(message)
    if ctx_fn is not None:
        if ctx_body is None:
            await _send_chat_output(message, "❌ Could not read **`adaptive-dm-context.txt`**.")
            return True
        pasted = ctx_body.strip()
    else:
        file_body = await _read_first_txt_attachment(message)
        pasted = (file_body.strip() if file_body is not None else text)

    if not pasted:
        await _send_chat_output(
            message,
            "Reply with **manual notes**, or attach **`adaptive-dm-context.txt`** exactly named to replace the whole block. "
            "Say **`reset manual`** to clear.",
        )
        return True

    low = pasted.lower()
    if low in ("reset", "reset manual", "clear", "clear manual"):
        adaptive_dm_manager.clear_profile_manual_override(uid)
        await _send_chat_output(message, "✅ Cleared. Manual text, full context override, prefix, and pending previews removed.")
        return True

    if ctx_fn is not None:
        ok, err_code, body_core = adaptive_dm_manager.validate_full_context_attachment(ctx_fn, pasted)
        if not ok:
            hints_fr = {
                "bad_filename": "Attachment must be named exactly **`adaptive-dm-context.txt`**.",
                "empty": "That file is empty.",
                "bad_suffix": "The file must end with the same **fixed behaviour** block as the export from **`/adaptive-status`**.",
                "missing_auto_header": "The file must include the line **User-specific context (auto):**.",
            }
            await _send_chat_output(message, f"❌ Invalid file: {hints_fr.get(err_code, err_code)}")
            return True
        prev_prof = adaptive_dm_manager.get_profile_data_copy(uid)
        prev_override = adaptive_dm_manager.get_context_override_body(uid)
        prev_prefix = adaptive_dm_manager.get_context_manual_prefix(uid)
        prev_legacy = adaptive_dm_manager.get_profile_manual_override(uid)
        auto_blk = adaptive_dm_manager.get_auto_profile_prompt_text(uid)
        preview_body = (
            f"{body_core.strip()}\n\n{auto_blk}\n\n{ADAPTIVE_DM_SYSTEM_SUFFIX.strip()}".strip()
            if auto_blk
            else f"{body_core.strip()}\n\n{ADAPTIVE_DM_SYSTEM_SUFFIX.strip()}".strip()
        )
        file_bytes = preview_body.encode("utf-8")
        if len(file_bytes) > 7_900_000:
            await _send_chat_output(message, "❌ That file is too large for Discord.")
            return True
        adaptive_dm_manager.set_pending_manual_merge(
            uid,
            {
                "kind": "full_replace",
                "previous_profile": prev_prof,
                "previous_override_body": prev_override,
                "previous_prefix": prev_prefix,
                "previous_legacy_manual": prev_legacy,
                "new_body_core": body_core.strip(),
            },
        )
        view = AdaptiveManualMergeConfirmView(owner_id=uid, timeout=600.0)
        await _send_chat_output(
            message,
            "Preview: your file plus the **current** auto-learned block. **Confirm** replaces stored context entirely and resets the structured profile so DM tuning refills the auto section. **Revert** undoes.",
            file=discord.File(io.BytesIO(file_bytes), filename="adaptive-dm-context-preview.txt"),
            view=view,
        )
        return True

    ok, err_code, manual_inner = adaptive_dm_manager.parse_manual_merge_reply(uid, pasted)
    if not ok:
        hints = {
            "empty": "Send some text with your notes, or attach a `.txt`.",
            "too_short": "Your notes are too short to merge meaningfully.",
            "full_file_incomplete": (
                "That looks like a **full export** but the tail is wrong or truncated. "
                "Either send **only your manual notes**, or paste the complete file including the fixed tail from **`/adaptive-status`**."
            ),
            "auto_mismatch": (
                "Full export’s auto-learned block does not match your current profile. "
                "Run **`/adaptive-status`** again, or reply with **manual notes only**."
            ),
        }
        msg = hints.get(err_code, "Could not use that reply. Try manual notes only, or a complete export.")
        await _send_chat_output(message, f"❌ {msg}")
        return True

    prev_snapshot = adaptive_dm_manager.get_profile_data_copy(uid)
    merge_ok, new_profile, merge_err = await merge_adaptive_manual_guidance_into_profile(
        uid,
        current_profile=prev_snapshot,
        manual_guidance=manual_inner,
    )
    if not merge_ok:
        await _send_chat_output(
            message,
            f"❌ Could not merge your guidance into the profile ({merge_err}). Try a shorter edit or try again.",
        )
        return True

    preview_body = adaptive_dm_manager.build_full_addition_for_profile_dict(uid, new_profile)
    file_bytes = preview_body.encode("utf-8")
    if len(file_bytes) > 7_900_000:
        await _send_chat_output(message, "❌ Preview file too large for Discord. Shorten your manual notes.")
        return True

    adaptive_dm_manager.set_pending_manual_merge(
        uid,
        {
            "kind": "manual_merge",
            "previous_profile": prev_snapshot,
            "new_profile": new_profile,
        },
    )
    view = AdaptiveManualMergeConfirmView(owner_id=uid, timeout=600.0)
    await _send_chat_output(
        message,
        "Here is the **proposed** adaptive context after folding in your edits (auto-learned block only — manual text is not stored as-is). "
        "**Confirm** to apply, **Revert** to keep your previous profile.",
        file=discord.File(io.BytesIO(file_bytes), filename="adaptive-dm-context-preview.txt"),
        view=view,
    )
    return True


def _adaptive_utility_intent(text: str) -> Optional[str]:
    """Detect adaptive-DM direct utility intents (no slash commands)."""
    t = (text or "").strip()
    if not t or t.startswith("/"):
        return None
    low = t.lower()
    if low.startswith("translate ") or low.startswith("translation:") or " translate this" in low:
        return "translate"
    if any(
        low.startswith(p)
        for p in (
            "analyze ",
            "analyse ",
            "ocr ",
            "code review ",
            "code-review ",
            "examine ",
            "interrogate ",
            "compare files",
            "compare these files",
            "compare the files",
        )
    ):
        if low.startswith("compare"):
            return "compare_files"
        return "analyze"
    if "dm history" in low or "history cutoff" in low or re.search(r"\bset\s+(?:dm\s+)?history\s+\d+\b", low):
        return "dm_history"
    return None


async def _handle_adaptive_utility_commands(client: discord.Client, message: discord.Message, clean_content: str) -> bool:
    """Run file analysis / compare / translate / dm-history from natural language in adaptive DMs."""
    intent = _adaptive_utility_intent(clean_content)
    if not intent:
        return False
    uid = message.author.id
    ch_id = message.channel.id
    uname = str(message.author.name)
    atts = await _read_message_attachments(message)

    if intent == "translate":
        body = clean_content.strip()
        if body.lower().startswith("translate "):
            body = body[10:].strip()
        elif body.lower().startswith("translation:"):
            body = body.split(":", 1)[-1].strip()
        target = "English"
        m_lang = re.search(r"\b(?:into|to)\s+([A-Za-z][A-Za-z\s\-]{2,40})\s*$", body, flags=re.IGNORECASE)
        if m_lang:
            target = m_lang.group(1).strip()
            body = body[: m_lang.start()].strip()
        if not body.strip():
            await _send_chat_output(message, "Say what to translate (optionally end with **to Finnish** / **into Spanish**, etc.).")
            return True
        out = await do_translate(uid, body, target)
        if not out:
            await _send_chat_output(message, "❌ Translation failed.")
            return True
        await _send_chat_output(message, out[:1900])
        return True

    if intent == "dm_history":
        low = clean_content.lower()
        if "summarize" in low and "history" in low:
            try:
                result = await compact_dm_history_for_channel(uid, ch_id, uname, force=True)
            except Exception as exc:
                await _send_chat_output(message, f"❌ Summarize failed: {str(exc)[:200]}")
                return True
            if result and result.get("compacted"):
                await _send_chat_output(
                    message,
                    f"✅ Rolled off **{result.get('merged_messages', 0)}** older turns into topic memory.",
                )
            else:
                await _send_chat_output(
                    message,
                    f"ℹ️ Nothing to compact ({(result or {}).get('reason', 'ok')}).",
                )
            return True
        m_set = re.search(r"\bset\s+(?:dm\s+)?history\s+(\d+)\b", low)
        if m_set:
            try:
                n = int(m_set.group(1))
            except ValueError:
                n = 0
            if n < 4 or n > 80:
                await _send_chat_output(message, "❌ Cutoff must be between **4** and **80** user turns.")
                return True
            conversation_manager.set_dm_history_cutoff(ch_id, n)
            conversation_manager.save()
            await _send_chat_output(message, f"✅ DM rolling history cutoff set to **{n}** user turns.")
            return True
        co = conversation_manager.get_dm_history_cutoff(ch_id)
        topics = conversation_manager.get_dm_topics(ch_id)
        await _send_chat_output(
            message,
            f"DM rolling cutoff: **{co}** user turns kept hot.\n"
            f"Topic memory entries: **{len(topics)}** (stale topics drop after ~30 days idle).\n"
            "Say **`set history 12`** to change the cutoff.",
        )
        return True

    if intent == "compare_files":
        if len(atts) < 2:
            await _send_chat_output(message, "Attach **at least two** text-y files to compare.")
            return True
        prompt = ""
        low = clean_content.lower()
        if "compare files" in low:
            prompt = clean_content[low.find("compare files") + len("compare files") :].strip()
        elif "compare these files" in low:
            prompt = clean_content[low.find("compare these files") + len("compare these files") :].strip()
        elif "compare the files" in low:
            prompt = clean_content[low.find("compare the files") + len("compare the files") :].strip()
        try:
            res = await compare_files(
                uid,
                ch_id,
                [{"filename": a["filename"], "data": a["data"]} for a in atts[:4]],
                prompt,
                uname,
                record_in_conversation=False,
            )
        except Exception as exc:
            await _send_chat_output(message, f"❌ Compare failed: {str(exc)[:200]}")
            return True
        await _send_chat_output(message, res[:1900] if res else "❌ Empty result.")
        return True

    # analyze / ocr / code-review / examine / interrogate
    if not atts:
        await _send_chat_output(message, "Attach a file for that, or say what to run more plainly.")
        return True
    file_info = atts[0]
    fn = file_info["filename"]
    data = file_info["data"]
    low = clean_content.lower()
    vision_mode = "concise"
    user_prompt = ""
    if low.startswith("ocr ") or low.startswith("ocr\n"):
        user_prompt = f"Extract ALL text. Language hint from user message: {clean_content[4:120].strip() or 'auto'}"
        vision_mode = "concise"
    elif low.startswith("code review ") or low.startswith("code-review "):
        rest = clean_content.split(" ", 2)[-1] if low.startswith("code review ") else clean_content.split(" ", 1)[-1]
        user_prompt = f"Review this code. Focus: {rest.strip()[:500]}" if rest.strip() else "Review this code."
    elif low.startswith("examine "):
        user_prompt = clean_content[8:].strip() or "Describe this image in full detail."
        vision_mode = "examine"
    elif low.startswith("interrogate "):
        user_prompt = clean_content[12:].strip() or "Answer very concisely."
        vision_mode = "interrogate"
    elif low.startswith("analyze ") or low.startswith("analyse "):
        user_prompt = clean_content.split(" ", 1)[1].strip() if " " in clean_content else ""
    else:
        user_prompt = ""

    try:
        res = await analyze_file(
            uid,
            ch_id,
            fn,
            data,
            user_prompt,
            uname,
            vision_mode=vision_mode,
            return_only_text=False,
            record_in_conversation=False,
        )
    except Exception as exc:
        await _send_chat_output(message, f"❌ File analysis failed: {str(exc)[:200]}")
        return True
    await _send_chat_output(message, res[:1900] if res else "❌ Empty result.")
    return True


async def _handle_adaptive_command_flow(client: discord.Client, message: discord.Message, clean_content: str) -> bool:
    """DM-only natural-language command routing with explicit confirmation."""
    user_id = message.author.id
    pending = adaptive_dm_manager.get_pending_confirmation(user_id)
    pref_update = _extract_no_confirm_preference(clean_content)
    if pref_update:
        cmd_name, enabled = pref_update
        is_pending_target = cmd_name == "__PENDING__"
        if cmd_name == "__PENDING__":
            pending_ref = pending or {}
            cmd_name = _normalize_command_name(str(pending_ref.get("command", "") or ""))
        if cmd_name:
            if enabled:
                adaptive_dm_manager.add_trusted_command(user_id, cmd_name)
                await _send_chat_output(message, f"Cool, I will run `/{cmd_name}` without asking next time.")
            else:
                adaptive_dm_manager.remove_trusted_command(user_id, cmd_name)
                await _send_chat_output(message, f"Got it, I will ask before running `/{cmd_name}`.")
            # If user also confirmed in the same message, run pending command right away.
            if pending and _is_positive_confirmation(clean_content):
                command_name = str(pending.get("command", "")).strip().lower()
                args = pending.get("arguments", {}) if isinstance(pending.get("arguments"), dict) else {}
                adaptive_dm_manager.clear_pending_confirmation(user_id)
                return await _execute_planned_command(
                    client,
                    message,
                    command_name,
                    args,
                    not_found_message=f"❌ I can no longer find `/{command_name}`.",
                    failure_message="❌ Couldn't run",
                )
            return True
        if is_pending_target:
            await _send_chat_output(message, "I can apply that once there is a pending command to confirm.")
            return True

    if pending:
        if _is_positive_confirmation(clean_content):
            command_name = str(pending.get("command", "")).strip().lower()
            args = pending.get("arguments", {}) if isinstance(pending.get("arguments"), dict) else {}
            adaptive_dm_manager.clear_pending_confirmation(user_id)
            return await _execute_planned_command(
                client,
                message,
                command_name,
                args,
                not_found_message=f"❌ I can no longer find `/{command_name}`.",
                failure_message="❌ I couldn't run",
            )
        if _looks_like_disable_confirmation_phrase(clean_content):
            command_name = str(pending.get("command", "")).strip().lower()
            if command_name:
                adaptive_dm_manager.add_trusted_command(user_id, command_name)
            args = pending.get("arguments", {}) if isinstance(pending.get("arguments"), dict) else {}
            adaptive_dm_manager.clear_pending_confirmation(user_id)
            return await _execute_planned_command(
                client,
                message,
                command_name,
                args,
                not_found_message=f"❌ I can no longer find `/{command_name}`.",
                failure_message="❌ Couldn't run",
            )
        if _is_negative_confirmation(clean_content):
            adaptive_dm_manager.clear_pending_confirmation(user_id)
            await _send_chat_output(message, "No worries, cancelled.")
            return True
        await _send_chat_output(message, "Quick yes/no: should I run it?")
        return True

    try:
        if await _handle_adaptive_utility_commands(client, message, clean_content):
            return True
    except Exception:
        pass

    if _looks_like_himas_request(clean_content):
        command_obj = client.tree.get_command("himas")
        if command_obj is not None:
            himas_text = _normalize_himas_command_text(clean_content)
            args = {"command": himas_text}
            if adaptive_dm_manager.is_trusted_no_confirm(user_id, "himas"):
                try:
                    kwargs = _build_kwargs_from_plan(client, message, command_obj, args)
                    interaction = _MessageInteractionProxy(client, message, command_obj.name)
                    await command_obj.callback(interaction, **kwargs)
                except Exception as exc:
                    await _send_chat_output(message, f"❌ Couldn't run `/himas`: {str(exc)[:200]}")
                return True
            adaptive_dm_manager.set_pending_confirmation(
                user_id,
                {"command": "himas", "arguments": args, "risk": "safe"},
            )
            await _send_chat_output(
                message,
                f"Want me to do this via Home Assistant?\n`/himas command:{himas_text}`\nReply `yes` or `no`.",
            )
            return True

    if not _looks_like_command_request(clean_content):
        return False
    plan = _quick_command_plan_from_text(clean_content)
    if not plan:
        schema = build_adaptive_command_schema(_build_command_schema(client))
        plan = await plan_command_from_text(user_id, clean_content, schema)
    if not plan.get("should_execute"):
        return False

    command_name = str(plan.get("command", "")).strip().lower()
    command_obj = client.tree.get_command(command_name)
    if command_obj is None:
        return False
    args = plan.get("arguments", {}) if isinstance(plan.get("arguments"), dict) else {}
    risk = str(plan.get("risk", "safe")).strip().lower()
    reason = str(plan.get("reason", "")).strip()
    if adaptive_dm_manager.is_trusted_no_confirm(user_id, command_obj.name):
        try:
            kwargs = _build_kwargs_from_plan(client, message, command_obj, args)
            interaction = _MessageInteractionProxy(client, message, command_obj.name)
            await command_obj.callback(interaction, **kwargs)
        except Exception as exc:
            await _send_chat_output(message, f"❌ Couldn't run `/{command_obj.name}`: {str(exc)[:200]}")
        return True

    adaptive_dm_manager.set_pending_confirmation(
        user_id,
        {"command": command_obj.name, "arguments": args, "risk": risk},
    )
    pretty_args = ", ".join(f"{k}={v}" for k, v in args.items()) if args else "no arguments"
    risk_note = "Heads up, this one can be sensitive. " if risk in {"risky", "dangerous"} else ""
    await _send_chat_output(
        message,
        f"{risk_note}Looks like you want `/{command_obj.name}` ({pretty_args}).\n"
        f"{(reason + chr(10)) if reason else ''}"
        "Say `yes` to run it, `no` to skip.\n"
        f"If you want me to stop asking for this command, say: `no confirmation for /{command_obj.name}`."
    )
    return True

async def process_discord_message(client, message, permission, conversation_manager) -> bool:
    """Process Discord messages with group chat awareness. Return True if handled."""
    config = get_config()
    wake_word = config.get("wake_word", "robot").lower()
    message_lower = (message.content or "").lower()
    
    # Check activation methods
    raw_content = (message.content or "").strip()
    is_wake_word = (message_lower == wake_word or 
                    message_lower.startswith(wake_word + " "))
    # If wake word itself starts with "!" (e.g. "!d"), wake-word chat must take precedence.
    is_admin_bang = message.author.id == PERMANENT_ADMIN and raw_content.startswith("!") and not is_wake_word
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user.mentioned_in(message)
    is_reply_to_bot = conversation_manager.is_continuation(message)
    
    # Only process if activated
    if not (is_wake_word or is_dm or is_mentioned or is_reply_to_bot or is_admin_bang):
        return False

    if is_dm:
        try:
            label = (getattr(message.author, "global_name", None) or message.author.name or "").strip()
            adaptive_dm_manager.touch_adaptive_sync_display_name(message.author.id, label)
        except Exception:
            pass

    # Extract clean content
    if is_wake_word:
        clean_content = message.content[len(wake_word):].strip()
    elif is_admin_bang:
        clean_content = raw_content[1:].strip()
    else:
        clean_content = message.content.replace(f'<@{client.user.id}>', '').strip()

    # Reply to /adaptive-status may be attachment-only; handle before the generic "analyze this file" fallback.
    if is_dm:
        try:
            if await _try_handle_dm_status_reply(client, message):
                return True
        except Exception:
            pass

    if not clean_content and message.attachments:
        clean_content = "Please analyze this file and use it as context for our conversation."
    if not clean_content:
        return True
    
    # Parse command for wake word / admin !command (only for non-continuations)
    if is_admin_bang and not is_reply_to_bot:
        if await _process_admin_bang_slash_command(client, message, clean_content):
            return True

    if is_wake_word and not is_reply_to_bot:
        parts = clean_content.split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        command_content = parts[1] if len(parts) > 1 else ""
        
        # Try to process as admin/himas/dl command
        processed = await process_wakeword_admin_command(
            client, message, command, command_content, permission
        )
        if processed:
            return True
        
        # "dl" = download media from last message/link and send to chat
        if command == "dl":
            processed = await process_wakeword_download(client, message, command_content)
            if processed:
                return True

    if is_dm and _looks_like_download_request(clean_content):
        if await process_wakeword_download(client, message, clean_content):
            return True

    if is_dm and adaptive_dm_manager.is_enabled(message.author.id):
        try:
            if await _handle_adaptive_command_flow(client, message, clean_content):
                return True
        except Exception:
            # If adaptive command routing fails, fall back to normal chat flow.
            pass

    if (
        is_dm
        and adaptive_dm_manager.is_enabled(message.author.id)
        and _adaptive_dm_explicit_image_intent(clean_content)
    ):
        try:
            if await _try_send_adaptive_dm_imagine(client, message, clean_content):
                return True
        except Exception:
            pass

    if is_dm:
        conversation_manager.touch_dm_user_activity(message.channel.id)

    async with message.channel.typing():
        # DM: continue rolling context only when replying to the bot or within a recent session window.
        dm_session_continue = conversation_manager.should_continue_dm_session(
            message.channel.id, is_reply_to_bot
        )
        is_continuation = is_reply_to_bot or (is_dm and dm_session_continue)
        if is_dm and adaptive_dm_manager.is_enabled(message.author.id) and not dm_session_continue:
            conversation_manager.reset_dm_transcript_only(message.channel.id)
        
        # Build attachments list (for files/images with wake word or mentions)
        attachments = await _read_message_attachments(message)
        reply_context_block: Optional[str] = None
        if (is_wake_word or is_mentioned) and message.reference:
            rcb, extra_ref = await _build_wake_message_reply_context(client, message)
            if rcb:
                reply_context_block = rcb
            if extra_ref:
                attachments = (attachments or []) + extra_ref
        
        # Include recent channel context for continuity and non-LLM bot messages (e.g. news posts).
        context = None
        if is_continuation and message.channel and hasattr(message.channel, 'history'):
            try:
                context = await get_chat_context(message.channel, limit=6, include_bots=is_dm)
            except Exception:
                context = None

        try:
            fast_reply_enabled = (not is_dm) or conversation_manager.is_dm_fast_reply_active(message.channel.id)
            answer = await asyncio.wait_for(
                ask_llm(
                    message.author.id,
                    message.channel.id,
                    clean_content,
                    str(message.author.name),
                    is_continuation=is_continuation,
                    platform="discord",
                    chat_context=context,
                    attachments=attachments if attachments else None,
                    is_dm=is_dm,
                    fast_reply=fast_reply_enabled,
                    reply_context_block=reply_context_block,
                ),
                timeout=150,
            )
        except asyncio.TimeoutError:
            timeout_count = reliability_telemetry.increment("llm_timeouts")
            await home_log.send_to_home(
                f"🔴 Message generation timed out (timeout #{timeout_count}) in channel {message.channel.id}. "
                f"user={message.author.id}. {reliability_telemetry.format_snapshot('Counters')}"
            )
            await _send_with_retry(
                lambda: _send_chat_output(message, "⚠️ I timed out while generating a reply. Please try again in a moment.")
            )
            return True
        except Exception as exc:
            error_count = reliability_telemetry.increment("llm_errors")
            await home_log.send_to_home(
                f"🔴 Message generation crashed (error #{error_count}) in channel {message.channel.id}. "
                f"user={message.author.id}. error={str(exc)[:280]}"
            )
            await _send_with_retry(
                lambda: _send_chat_output(message, "⚠️ I hit an internal error while generating a reply. Please try again.")
            )
            return True

        if not answer:
            error_count = reliability_telemetry.increment("llm_errors")
            await home_log.send_to_home(
                f"🔴 Message generation returned empty response (error #{error_count}) in channel {message.channel.id}. "
                f"user={message.author.id}. {reliability_telemetry.format_snapshot('Counters')}"
            )
            await _send_with_retry(
                lambda: _send_chat_output(message, "⚠️ I could not generate a response this time. Please try again.")
            )
            return True
        
        chunks = _chunk_message(answer, MAX_MESSAGE_LENGTH)
        response = None
        for i, chunk in enumerate(chunks):
            if i == 0:
                response = await _send_with_retry(lambda: _send_chat_output(message, chunk))
            else:
                response = await _send_with_retry(lambda: message.channel.send(chunk))
            conversation_manager.set_last_bot_message(message.channel.id, response.id)
            if i < len(chunks) - 1:
                await asyncio.sleep(_CHUNK_SEND_DELAY)
        
        # Save conversations periodically
        conversation_manager.save()
        if is_dm:
            _schedule_adaptive_post_reply_calibration(message, clean_content)
        return True

async def process_wakeword_download(client, message, link_or_empty):
    """Download media from link or last message with media, send to chat. Files not stored."""
    from config import get_download_limit_mb
    from commands.download._helpers import extract_urls, download_url_sync, DOWNLOAD_EXTENSIONS
    import os
    channel = message.channel
    max_bytes = get_download_limit_mb() * 1024 * 1024
    target_url = None
    target_attachment = None
    if link_or_empty and link_or_empty.strip():
        urls = extract_urls(link_or_empty)
        if urls:
            target_url = urls[0]
    if not target_url:
        try:
            async for msg in channel.history(limit=20):
                if msg.author.bot:
                    continue
                urls = extract_urls(msg.content or "")
                if urls:
                    target_url = urls[0]
                    break
                for att in msg.attachments:
                    name = (att.filename or "").lower()
                    if any(name.endswith(ext) for ext in DOWNLOAD_EXTENSIONS):
                        target_attachment = att
                        break
                if target_attachment:
                    break
        except Exception:
            pass
    if not target_url and not target_attachment:
        await _send_chat_output(message, "❌ No link or media found. Send a link or use `/download` after a message with media.")
        return True
    data, filename = None, None
    if target_attachment:
        try:
            data = await target_attachment.read()
            filename = target_attachment.filename
            if len(data) > max_bytes:
                await _send_chat_output(message, f"❌ File too large. Max: {get_download_limit_mb()} MB.")
                return True
        except Exception as e:
            await _send_chat_output(message, f"❌ Failed to read attachment: {e}")
            return True
    elif target_url:
        import asyncio
        data, filename = await asyncio.to_thread(download_url_sync, target_url, max_bytes)
        if data is None:
            await _send_chat_output(message, f"❌ Download failed: {filename}")
            return True
    if not data:
        await _send_chat_output(message, "❌ Nothing to send.")
        return True
    from utils.llm_service import ask_llm
    ext = os.path.splitext(filename or "")[1].lower()
    is_image = ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    try:
        if is_image:
            attachments = [{"filename": filename, "data": data}]
            reply = await ask_llm(
                message.author.id,
                channel.id,
                "Describe or analyze this image.",
                str(message.author.name),
                is_continuation=False,
                platform="discord",
                attachments=attachments,
                is_dm=isinstance(channel, discord.DMChannel),
            )
            await _send_chat_output(message, sanitize_discord_bot_content(reply or ""))
        else:
            from io import BytesIO
            await _send_chat_output(
                message,
                f"📥 Downloaded: **{filename}**",
                file=discord.File(filename=filename, fp=BytesIO(data)),
            )
    except Exception as e:
        await _send_chat_output(message, f"❌ Error: {e}")
    return True

async def process_wakeword_admin_command(client, message, command, content, permission):
    """Process admin commands triggered by wake word"""
    if command == "restart":
        await message.reply("🔄 Restarting...")
        subprocess.run(['sudo', 'systemctl', 'restart', 'dubot.service'])
        return True
    elif command == "kill":
        await message.reply("👋 Bye...")

        async def _delayed_exit():
            await asyncio.sleep(1.5)
            conversation_manager.save()
            reminder_manager.stop()
            sys.exit(0)

        asyncio.create_task(_delayed_exit())
        return True
    elif command == "himas" and permission in ["admin", "himas"]:
        if not content:
            return False
        answer = await ask_home_assistant(content)
        await message.reply(answer[:1900])
        return True
    elif command == "sleep" and message.author.id == PERMANENT_ADMIN:
        set_bot_awake(False)
        await message.reply("😴 Going offline. I will ignore everything except `/wake`.")
        return True
    elif command == "wake" and message.author.id == PERMANENT_ADMIN:
        set_bot_awake(True)
        await message.reply("✅ Awake and back online.")
        return True
    elif command == "checkwake" and message.author.id == PERMANENT_ADMIN:
        await message.reply(f"Current wake word: `{get_wake_word()}`")
        return True
    return False

async def get_chat_context(channel, limit=5, include_bots=False):
    """Get recent messages for group chat context"""
    messages = []
    try:
        async for msg in channel.history(limit=limit):
            if msg.author.bot and not include_bots:
                continue
            text = msg.content or ""
            if not text.strip() and not msg.attachments:
                continue
            if msg.attachments:
                attachment_names = ", ".join(a.filename for a in msg.attachments[:3] if a.filename)
                if attachment_names:
                    text = f"{text}\n[attachments: {attachment_names}]".strip()
            messages.append({
                "author": msg.author.name,
                "content": text,
                "timestamp": msg.created_at.isoformat()
            })
    except Exception:
        return []

    # Reverse to get chronological order
    messages.reverse()
    return messages
