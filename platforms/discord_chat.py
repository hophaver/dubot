import discord
import subprocess
import sys
import os
import asyncio
from config import get_config
from conversations import conversation_manager
from services.reminder_service import reminder_manager
from utils.llm_service import ask_llm
from utils.ha_integration import ask_home_assistant

async def process_discord_message(client, message, permission, conversation_manager):
    """Process Discord messages with group chat awareness"""
    config = get_config()
    wake_word = config.get("wake_word", "robot").lower()
    message_lower = message.content.lower()
    
    # Check activation methods
    is_wake_word = (message_lower == wake_word or 
                    message_lower.startswith(wake_word + " "))
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user.mentioned_in(message)
    is_reply_to_bot = conversation_manager.is_continuation(message)
    
    # Only process if activated
    if not (is_wake_word or is_dm or is_mentioned or is_reply_to_bot):
        return
    
    # Extract clean content
    if is_wake_word:
        clean_content = message.content[len(wake_word):].strip()
    else:
        clean_content = message.content.replace(f'<@{client.user.id}>', '').strip()
    
    if not clean_content:
        return
    
    # Parse command for wake word (only for non-continuations)
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
        
        # Wake word "dl" = download media from last message/link and send to chat
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
        if content:
            answer = await ask_home_assistant(content)
            await message.reply(answer[:1900])
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
