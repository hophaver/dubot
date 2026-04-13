import discord
import subprocess
import sys
import os
import asyncio
import inspect
import shlex
import re
from typing import Optional, Any, get_args, get_origin
from discord import app_commands
from config import get_config, get_wake_word, set_bot_awake
from conversations import conversation_manager
from services.reminder_service import reminder_manager
from utils.llm_service import ask_llm
from utils.ha_integration import ask_home_assistant
from integrations import PERMANENT_ADMIN


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
        await message.reply(_format_discord_admin_bang_help(client))
        return True

    parts = payload.split(maxsplit=1)
    raw_command = parts[0].lower().replace("_", "-")
    arg_text = parts[1] if len(parts) > 1 else ""
    command_obj = client.tree.get_command(raw_command)
    if command_obj is None:
        await message.reply(
            f"❌ Unknown command `!{raw_command}`.\nUse `/help` for all commands."
        )
        return True

    try:
        kwargs = _parse_admin_bang_kwargs(client, message, command_obj, arg_text)
    except Exception as exc:
        await message.reply(
            f"❌ Invalid syntax for `!{command_obj.name}`: {str(exc)}\n"
            f"Format: `{_build_command_usage(command_obj)}`"
        )
        return True

    interaction = _MessageInteractionProxy(client, message, command_obj.name)
    try:
        await command_obj.callback(interaction, **kwargs)
    except TypeError as exc:
        await message.reply(
            f"❌ Invalid syntax for `!{command_obj.name}`.\n"
            f"Format: `{_build_command_usage(command_obj)}`\n"
            f"Details: {str(exc)[:180]}"
        )
    except Exception as exc:
        await message.reply(
            f"❌ Command `!{command_obj.name}` failed: {str(exc)[:200]}\n"
            f"Format: `{_build_command_usage(command_obj)}`"
        )
    return True

async def process_discord_message(client, message, permission, conversation_manager):
    """Process Discord messages with group chat awareness"""
    config = get_config()
    wake_word = config.get("wake_word", "robot").lower()
    message_lower = message.content.lower()
    
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
        return
    
    # Extract clean content
    if is_wake_word:
        clean_content = message.content[len(wake_word):].strip()
    elif is_admin_bang:
        clean_content = raw_content[1:].strip()
    else:
        clean_content = message.content.replace(f'<@{client.user.id}>', '').strip()
    
    if not clean_content:
        return
    
    # Parse command for wake word / admin !command (only for non-continuations)
    if is_admin_bang and not is_reply_to_bot:
        if await _process_admin_bang_slash_command(client, message, clean_content):
            return

    if is_wake_word and not is_reply_to_bot:
        parts = clean_content.split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        command_content = parts[1] if len(parts) > 1 else ""
        
        # Try to process as admin/himas/dl command
        processed = await process_wakeword_admin_command(
            client, message, command, command_content, permission
        )
        if processed:
            return
        
        # "dl" = download media from last message/link and send to chat
        if command == "dl":
            processed = await process_wakeword_download(client, message, command_content)
            if processed:
                return

    async with message.channel.typing():
        # Determine if this is a continuation
        is_continuation = is_reply_to_bot
        
        # Build attachments list (for files/images with wake word or mentions)
        attachments = []
        if message.attachments:
            for att in message.attachments:
                try:
                    data = await att.read()
                    attachments.append({"filename": att.filename, "data": data})
                except Exception:
                    pass
        
        # Only pass channel context when continuing a chat (reply to bot). Wake word = fresh chat, no prior context.
        context = None
        if is_continuation and not is_dm and message.channel and hasattr(message.channel, 'history'):
            try:
                context = await get_chat_context(message.channel, limit=5)
            except Exception:
                context = None

        answer = await ask_llm(
            message.author.id,
            message.channel.id,
            clean_content,
            str(message.author.name),
            is_continuation=is_continuation,
            platform="discord",
            chat_context=context,
            attachments=attachments if attachments else None
        )
        
        from commands.shared import _chunk_message, MAX_MESSAGE_LENGTH
        import asyncio
        chunks = _chunk_message(answer, MAX_MESSAGE_LENGTH)
        response = None
        for i, chunk in enumerate(chunks):
            if i == 0:
                response = await message.reply(chunk)
            else:
                response = await message.channel.send(chunk)
            conversation_manager.set_last_bot_message(message.channel.id, response.id)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)
        
        # Save conversations periodically
        conversation_manager.save()

async def process_wakeword_download(client, message, link_or_empty):
    """Download media from link or last message with media, send to chat. Files not stored."""
    from config import get_download_limit_mb
    from commands.download import _extract_urls, _download_url_sync, DOWNLOAD_EXTENSIONS
    import os
    channel = message.channel
    max_bytes = get_download_limit_mb() * 1024 * 1024
    target_url = None
    target_attachment = None
    if link_or_empty and link_or_empty.strip():
        urls = _extract_urls(link_or_empty)
        if urls:
            target_url = urls[0]
    if not target_url:
        try:
            async for msg in channel.history(limit=20):
                if msg.author.bot:
                    continue
                urls = _extract_urls(msg.content or "")
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
        await message.reply("❌ No link or media found. Send a link or use `/download` after a message with media.")
        return True
    data, filename = None, None
    if target_attachment:
        try:
            data = await target_attachment.read()
            filename = target_attachment.filename
            if len(data) > max_bytes:
                await message.reply(f"❌ File too large. Max: {get_download_limit_mb()} MB.")
                return True
        except Exception as e:
            await message.reply(f"❌ Failed to read attachment: {e}")
            return True
    elif target_url:
        import asyncio
        data, filename = await asyncio.to_thread(_download_url_sync, target_url, max_bytes)
        if data is None:
            await message.reply(f"❌ Download failed: {filename}")
            return True
    if not data:
        await message.reply("❌ Nothing to send.")
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
            await message.reply(reply)
        else:
            from io import BytesIO
            await message.reply(
                f"📥 Downloaded: **{filename}**",
                file=discord.File(filename=filename, fp=BytesIO(data)),
            )
    except Exception as e:
        await message.reply(f"❌ Error: {e}")
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

async def get_chat_context(channel, limit=5):
    """Get recent messages for group chat context"""
    messages = []
    try:
        async for msg in channel.history(limit=limit):
            if msg.author.bot:
                continue
            messages.append({
                "author": msg.author.name,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat()
            })
    except Exception:
        return []

    # Reverse to get chronological order
    messages.reverse()
    return messages
