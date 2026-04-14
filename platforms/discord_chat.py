import discord
import subprocess
import sys
import os
import asyncio
import inspect
import shlex
import re
from typing import Optional, Any, Dict, get_args, get_origin
from discord import app_commands
from config import get_config, get_wake_word, set_bot_awake
from conversations import conversation_manager
from services.reminder_service import reminder_manager
from utils.llm_service import ask_llm, plan_command_from_text
from utils.ha_integration import ask_home_assistant
from utils import home_log
from utils import reliability_telemetry
from integrations import PERMANENT_ADMIN
from jarvis import jarvis_manager


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


def _schedule_jarvis_post_reply_calibration(message: discord.Message, message_text: str):
    """Queue user-only samples and periodically update Jarvis tone after replying."""
    user_id = getattr(message.author, "id", None)
    if user_id is None:
        return
    if not message_text:
        return
    if not jarvis_manager.is_enabled(user_id):
        return

    async def _runner():
        try:
            await asyncio.to_thread(jarvis_manager.queue_user_message_for_tuning, user_id, message_text)
            updated = await asyncio.to_thread(jarvis_manager.run_tone_tuning_now, user_id, False)
            if updated and isinstance(message.channel, discord.DMChannel):
                await _send_chat_output(message, "I just fine-tuned my tone from your recent messages.")
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
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(word in t for word in ["light", "lamp", "kitchen", "bedroom", "living room", "thermostat", "temperature", "switch", "fan", "ceiling", "heater", "ac"]):
        return any(verb in t for verb in ["turn on", "turn off", "set", "dim", "toggle", "what is", "status"])
    return False


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
    t = re.sub(r"^(jarvis[,:\s]+)?", "", t, flags=re.IGNORECASE).strip()
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
    t = (text or "").strip().lower()
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


async def _handle_jarvis_command_flow(client: discord.Client, message: discord.Message, clean_content: str) -> bool:
    """DM-only natural-language command routing with explicit confirmation."""
    user_id = message.author.id
    pending = jarvis_manager.get_pending_confirmation(user_id)
    pref_update = _extract_no_confirm_preference(clean_content)
    if pref_update:
        cmd_name, enabled = pref_update
        is_pending_target = cmd_name == "__PENDING__"
        if cmd_name == "__PENDING__":
            pending_ref = pending or {}
            cmd_name = _normalize_command_name(str(pending_ref.get("command", "") or ""))
        if cmd_name:
            if enabled:
                jarvis_manager.add_trusted_command(user_id, cmd_name)
                await _send_chat_output(message, f"Cool, I will run `/{cmd_name}` without asking next time.")
            else:
                jarvis_manager.remove_trusted_command(user_id, cmd_name)
                await _send_chat_output(message, f"Got it, I will ask before running `/{cmd_name}`.")
            # If user also confirmed in the same message, run pending command right away.
            if pending and _is_positive_confirmation(clean_content):
                command_name = str(pending.get("command", "")).strip().lower()
                args = pending.get("arguments", {}) if isinstance(pending.get("arguments"), dict) else {}
                jarvis_manager.clear_pending_confirmation(user_id)
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
            jarvis_manager.clear_pending_confirmation(user_id)
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
                jarvis_manager.add_trusted_command(user_id, command_name)
            args = pending.get("arguments", {}) if isinstance(pending.get("arguments"), dict) else {}
            jarvis_manager.clear_pending_confirmation(user_id)
            return await _execute_planned_command(
                client,
                message,
                command_name,
                args,
                not_found_message=f"❌ I can no longer find `/{command_name}`.",
                failure_message="❌ Couldn't run",
            )
        if _is_negative_confirmation(clean_content):
            jarvis_manager.clear_pending_confirmation(user_id)
            await _send_chat_output(message, "No worries, cancelled.")
            return True
        await _send_chat_output(message, "Quick yes/no: should I run it?")
        return True

    if _looks_like_himas_request(clean_content):
        command_obj = client.tree.get_command("himas")
        if command_obj is not None:
            himas_text = _normalize_himas_command_text(clean_content)
            args = {"command": himas_text}
            if jarvis_manager.is_trusted_no_confirm(user_id, "himas"):
                try:
                    kwargs = _build_kwargs_from_plan(client, message, command_obj, args)
                    interaction = _MessageInteractionProxy(client, message, command_obj.name)
                    await command_obj.callback(interaction, **kwargs)
                except Exception as exc:
                    await _send_chat_output(message, f"❌ Couldn't run `/himas`: {str(exc)[:200]}")
                return True
            jarvis_manager.set_pending_confirmation(
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
        schema = _build_command_schema(client)
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
    if jarvis_manager.is_trusted_no_confirm(user_id, command_obj.name):
        try:
            kwargs = _build_kwargs_from_plan(client, message, command_obj, args)
            interaction = _MessageInteractionProxy(client, message, command_obj.name)
            await command_obj.callback(interaction, **kwargs)
        except Exception as exc:
            await _send_chat_output(message, f"❌ Couldn't run `/{command_obj.name}`: {str(exc)[:200]}")
        return True

    jarvis_manager.set_pending_confirmation(
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
    
    # Extract clean content
    if is_wake_word:
        clean_content = message.content[len(wake_word):].strip()
    elif is_admin_bang:
        clean_content = raw_content[1:].strip()
    else:
        clean_content = message.content.replace(f'<@{client.user.id}>', '').strip()
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

    if is_dm and jarvis_manager.is_enabled(message.author.id):
        try:
            if await _handle_jarvis_command_flow(client, message, clean_content):
                return True
        except Exception:
            # If Jarvis planner fails, fall back to normal chat flow.
            pass

    async with message.channel.typing():
        # Determine if this is a continuation
        is_continuation = is_reply_to_bot or is_dm
        
        # Build attachments list (for files/images with wake word or mentions)
        attachments = await _read_message_attachments(message)
        
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
        
        from commands.shared import _chunk_message, MAX_MESSAGE_LENGTH
        chunks = _chunk_message(answer, MAX_MESSAGE_LENGTH)
        response = None
        for i, chunk in enumerate(chunks):
            if i == 0:
                response = await _send_with_retry(lambda: _send_chat_output(message, chunk))
            else:
                response = await _send_with_retry(lambda: message.channel.send(chunk))
            conversation_manager.set_last_bot_message(message.channel.id, response.id)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)
        
        # Save conversations periodically
        conversation_manager.save()
        if is_dm:
            _schedule_jarvis_post_reply_calibration(message, clean_content)
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
            )
            await _send_chat_output(message, reply)
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
