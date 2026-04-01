import asyncio
import discord
import sys
import os
import signal
import time
import random
from discord import app_commands
from integrations import DISCORD_BOT_TOKEN, PERMANENT_ADMIN
from config import (
    get_config,
    get_startup_channel_id,
    get_wake_word,
    get_current_persona,
    get_conversation_channels,
    get_conversation_frequency,
)
from whitelist import get_user_permission
from conversations import conversation_manager
from services.reminder_service import reminder_manager
from platforms.discord_chat import process_discord_message
from commands.shitpost import handle_shitpost
from utils.llm_service import initialize_command_database
from utils import home_log
from commands.shared import send_long_to_channel

# Initialize command database
initialize_command_database()

# Initialize intents and client
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

class BotClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.start_time = time.time()
        self.commands_registered = False

    async def close(self):
        try:
            from services.clone_service import revert_if_active
            await revert_if_active(self)
        except Exception:
            pass
        conversation_manager.save()
        reminder_manager.stop()
        await super().close()

    async def setup_hook(self):
        """Setup hook to sync commands"""
        # Only register commands if not already registered
        if not self.commands_registered:
            await self.register_all_commands()
            self.commands_registered = True
            
        # Set reminder service client
        reminder_manager.set_client(self)
    
    async def register_all_commands(self):
        """Register ALL commands without duplicates"""
        # Clear existing commands
        self.tree.clear_commands(guild=None)
        
        errors = []
        # 1. General Commands
        try:
            from commands.general import GeneralCommands
            GeneralCommands(self).register()
        except Exception as e:
            errors.append(f"general: {e}")
        # 2. File Analysis
        try:
            from commands.file import FileCommands
            FileCommands(self).register()
        except Exception as e:
            errors.append(f"file: {e}")
        # 3. Chat
        try:
            from commands.chat import ChatCommands
            ChatCommands(self).register()
        except Exception as e:
            errors.append(f"chat: {e}")
        # 4. Reminder
        try:
            from commands.reminder import ReminderCommands
            ReminderCommands(self).register()
        except Exception as e:
            errors.append(f"reminder: {e}")
        # 5. Persona
        try:
            from commands.persona import PersonaCommands
            PersonaCommands(self).register()
        except Exception as e:
            errors.append(f"persona: {e}")
        # 6. Model
        try:
            from commands.model import ModelCommands
            ModelCommands(self).register()
        except Exception as e:
            errors.append(f"model: {e}")
        # 6b. Download
        try:
            from commands.download import DownloadCommands
            DownloadCommands(self).register()
        except Exception as e:
            errors.append(f"download: {e}")
        # 6c. Translate
        try:
            from commands.translate import TranslateCommands
            TranslateCommands(self).register()
        except Exception as e:
            errors.append(f"translate: {e}")
        # 6d. Scripts
        try:
            from commands.scripts import ScriptsCommands
            ScriptsCommands(self).register()
        except Exception as e:
            errors.append(f"scripts: {e}")
        # 7. Admin
        try:
            from commands.admin import AdminCommands
            AdminCommands(self).register()
        except Exception as e:
            errors.append(f"admin: {e}")
        # 7b. Shitpost
        try:
            from commands.shitpost import ShitpostCommands
            ShitpostCommands(self).register()
        except Exception as e:
            errors.append(f"shitpost: {e}")
        # 7c. Ollama
        try:
            from commands.ollama import OllamaCommands
            OllamaCommands(self).register()
        except Exception as e:
            errors.append(f"ollama: {e}")
        # 8. Home Assistant
        try:
            from commands.ha import HACommands
            HACommands(self).register()
        except Exception as e:
            errors.append(f"ha: {e}")
        # 9. Help
        try:
            from commands.help import HelpCommands
            HelpCommands(self).register()
        except Exception as e:
            errors.append(f"help: {e}")

        await self.tree.sync()
        self._startup_errors = errors

client = BotClient()

# In-memory counters for auto-conversation per channel: {channel_id: {"count": int, "next": int}}
_auto_conversation_state = {}

# Event handlers
async def _run_startup_checks(client):
    """Collect all startup check results for the embed. Returns (errors, checks_dict)."""
    errors = getattr(client, "_startup_errors", [])

    # Commands
    cmd_status = "✅ All loaded" if not errors else "❌ " + ", ".join(errors)[:200]

    # Model
    from models import model_manager
    model_info = model_manager.get_user_model_info(0)
    model_status = f"{model_info.get('model', 'qwen2.5:7b')} ({model_info.get('provider', 'local')})"

    # Persona
    persona_status = get_current_persona()

    # Home Assistant
    ha_status = "○ Not configured"
    try:
        from integrations import HA_URL, HA_ACCESS_TOKEN
        if HA_URL and HA_URL.strip() and HA_ACCESS_TOKEN:
            from utils.ha_integration import ha_manager
            entities = await ha_manager.get_all_entities()
            ha_status = "✅ Connected" if entities else "⚠️ Disconnected"
        elif not HA_ACCESS_TOKEN:
            ha_status = "○ Not configured"
    except Exception:
        ha_status = "⚠️ Disconnected"

    # Location
    try:
        from integrations import LOCATION
        location_status = LOCATION if (LOCATION and LOCATION != "Unknown") else "○ Unknown"
    except Exception:
        location_status = "○ Unknown"

    # Wake word
    wake_status = get_wake_word()

    # Home channel
    home_status = "✅ Set" if get_startup_channel_id() else "○ Not set"

    # Status API (started before client.run)
    from services.status_server import PORT as STATUS_PORT
    status_api = f"http://localhost:{STATUS_PORT}/status"

    # Admin (global ping)
    admin_status = f"<@{PERMANENT_ADMIN}>"

    return errors, {
        "Commands": cmd_status,
        "Model": model_status,
        "Persona": persona_status,
        "Home Assistant": ha_status,
        "Location": location_status,
        "Wake word": wake_status,
        "Home channel": home_status,
        "Status API": status_api,
        "Admin": admin_status,
    }


@client.event
async def on_ready():
    """Called when bot is ready. Run startup checks and send a single embed to home."""
    home_log.set_client(client)
    from services.clone_service import on_bot_ready_baseline
    await on_bot_ready_baseline(client)
    config = get_config()
    status_text = config.get("bot_status", "Analyzing files with AI")
    activity = discord.Activity(type=discord.ActivityType.listening, name=status_text)
    await client.change_presence(activity=activity)

    errors, checks = await _run_startup_checks(client)
    has_issues = bool(errors)

    embed = discord.Embed(
        title="🤖 Bot started" if not has_issues else "🤖 Bot started (with issues)",
        color=discord.Color.green() if not has_issues else discord.Color.orange(),
        description="Startup checks:",
    )
    embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
    for name, value in checks.items():
        embed.add_field(name=name, value=value[:1024], inline=True)
    embed.set_footer(text=time.strftime("%Y-%m-%d %H:%M:%S"))

    sent = await home_log.send_to_home(embed=embed)
    if get_startup_channel_id() and not sent:
        await home_log.log("⚠️ Could not send startup message to home channel (check permissions).", also_send=False)

@client.event
async def on_message(message):
    """Handle incoming messages with file attachments"""
    if message.author == client.user:
        return

    from services.clone_service import mirror_message_if_clone
    if await mirror_message_if_clone(client, message):
        return

    # Ignore other bots for auto-conversation and permissions
    if message.author.bot:
        return

    # Get permission level
    permission = get_user_permission(message.author.id)
    if permission is None:
        return

    # Check if bot is mentioned and has file attachments
    if client.user.mentioned_in(message) and message.attachments:
        # Clean the message content (remove mention)
        content = message.content.replace(f'<@{client.user.id}>', '').strip()
        
        # If no text content, use default prompt
        if not content:
            content = "Please analyze these files"
        
        # Process each attachment
        for attachment in message.attachments:
            try:
                # Check file size (limit to 8MB for Discord API)
                if attachment.size > 8 * 1024 * 1024:
                    await message.channel.send(f"⚠️ File {attachment.filename} is too large (>8MB). Please use smaller files.")
                    continue
                
                # Download the file
                file_data = await attachment.read()
                
                # Analyze the file
                from utils.llm_service import analyze_file
                
                # Send initial processing message
                processing_msg = await message.channel.send(f"📄 Analyzing **{attachment.filename}**...")
                
                result = await analyze_file(
                    message.author.id,
                    message.channel.id,
                    attachment.filename,
                    file_data,
                    content,
                    str(message.author.name)
                )
                
                # Delete processing message
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                
                await send_long_to_channel(message.channel, result)
                    
            except Exception as e:
                await message.channel.send(f"❌ Error analyzing {attachment.filename}: {str(e)}")
        
        # Don't process further if we handled files
        return

    # Shitpost: !word or .word (single token, 3+ letters, letters only; not wake word)
    if await handle_shitpost(client, message):
        return

    # Passive auto-conversation in configured channels
    await _handle_auto_conversation(message)

    # Process Discord-specific chat (original functionality)
    await process_discord_message(client, message, permission, conversation_manager)


async def _handle_auto_conversation(message: discord.Message) -> None:
    """Occasionally continue conversation in configured channels without wake word."""
    try:
        channel_id = message.channel.id
    except AttributeError:
        return

    # Only in enabled channels
    if channel_id not in get_conversation_channels():
        return

    # Do not trigger on messages aimed directly at the bot
    content = (message.content or "").strip()
    content_lower = content.lower()
    wake_word = get_wake_word().lower()
    if client.user.mentioned_in(message):
        return
    if content_lower == wake_word or content_lower.startswith(wake_word + " "):
        return

    # Update per-channel counters and decide whether to trigger
    min_n, max_n = get_conversation_frequency()
    state = _auto_conversation_state.get(channel_id)
    if not state:
        state = {"count": 0, "next": random.randint(min_n, max_n)}
        _auto_conversation_state[channel_id] = state

    state["count"] += 1
    if state["count"] < state["next"]:
        return

    # Reset for next trigger
    state["count"] = 0
    state["next"] = random.randint(min_n, max_n)

    # Collect last 10 human text messages from this channel (excluding bots)
    messages = []
    try:
        async for msg in message.channel.history(limit=50):
            if msg.author.bot:
                continue
            text = (msg.content or "").strip()
            if not text:
                continue
            messages.append(
                {
                    "author": msg.author.name,
                    "content": text,
                    "timestamp": msg.created_at.isoformat(),
                }
            )
            if len(messages) >= 10:
                break
    except Exception as e:
        await home_log.log(f"Error fetching channel history for auto-conversation: {e}", also_send=False)
        return

    if not messages:
        return

    messages.reverse()  # chronological order

    # Ask LLM to continue conversation briefly, using global persona (user_id=0)
    from utils.llm_service import ask_llm

    prompt = (
        "Read the recent messages above and continue the conversation with one short, natural reply. "
        "Do not list what each person said or describe the conversation; just answer as if you are a real user in this chat."
    )

    try:
        answer = await ask_llm(
            0,
            channel_id,
            prompt,
            str(client.user.name),
            is_continuation=False,
            platform="discord",
            chat_context=messages,
        )
    except Exception as e:
        await home_log.log(f"Error generating auto-conversation reply: {e}", also_send=False)
        return

    if not answer:
        return

    # Send as a regular message (no reply)
    try:
        await message.channel.send(answer)
    except Exception as e:
        await home_log.log(f"Error sending auto-conversation reply: {e}", also_send=False)

@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle slash command errors; report to user and to home channel."""
    if isinstance(error, app_commands.CommandInvokeError):
        original = error.original
        error_msg = str(original)
        if "file" in error_msg.lower() or "attachment" in error_msg.lower():
            msg = "⚠️ File upload failed. The file might be too large or in an unsupported format."
        elif "400" in error_msg or "413" in error_msg:
            msg = "⚠️ Discord API error. File might be too large or there's a network issue."
        else:
            msg = f"❌ Command error: {(error_msg[:150] + '...') if len(error_msg) > 150 else error_msg}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.NotFound:
            pass
        cmd = getattr(interaction.command, "name", "?")
        await home_log.send_to_home(f"🔴 **/{cmd}** error: {error_msg[:500]}")
    elif isinstance(error, app_commands.CommandNotFound):
        try:
            await interaction.response.send_message("❌ Command not found. Use `/help` to see available commands.", ephemeral=True)
        except discord.NotFound:
            pass
    elif isinstance(error, app_commands.CommandOnCooldown):
        try:
            await interaction.response.send_message(f"⏰ Command is on cooldown. Try again in {error.retry_after:.1f} seconds.", ephemeral=True)
        except discord.NotFound:
            pass
    elif isinstance(error, app_commands.MissingPermissions):
        try:
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        except discord.NotFound:
            pass
    else:
        try:
            await interaction.response.send_message(f"❌ Error: {str(error)[:150]}", ephemeral=True)
        except discord.NotFound:
            pass
        await home_log.send_to_home(f"🔴 **App command** error: {str(error)[:500]}")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print("\n👋 Received shutdown signal, cleaning up...")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        conversation_manager.save()
        reminder_manager.stop()
        sys.exit(0)
        return
    if not client.is_closed():
        loop.create_task(client.close())
    else:
        conversation_manager.save()
        reminder_manager.stop()
        sys.exit(0)

def _ensure_ollama_running():
    """If config says start_ollama_on_startup and Ollama is not responding, start ollama serve in background."""
    try:
        if not get_config().get("start_ollama_on_startup"):
            return
        from utils.ollama import check_ollama_running, start_ollama
        if check_ollama_running():
            return
        start_ollama()
    except Exception:
        pass


if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Ensure data directory exists
    os.makedirs("data", exist_ok=True)
    os.makedirs("data/files", exist_ok=True)
    os.makedirs("assets", exist_ok=True)
    os.makedirs("web", exist_ok=True)

    # Start HTTP server for GET /status (localhost:3000/status)
    from services.status_server import start_status_server
    start_status_server()

    # Optionally start Ollama in background so bot doesn't hang waiting for it
    _ensure_ollama_running()

    # Start reminder service
    reminder_manager.start()

    try:
        client.run(DISCORD_BOT_TOKEN)
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
        conversation_manager.save()
        reminder_manager.stop()
        sys.exit(0)
    except discord.errors.LoginFailure:
        print("❌ Invalid Discord token. Check your DISCORD_BOT_TOKEN in integrations.py")
        reminder_manager.stop()
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        import traceback
        traceback.print_exc()
        reminder_manager.stop()
        sys.exit(1)
