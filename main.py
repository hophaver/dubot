import sys
import os

os.environ["DUBOT_RUNTIME"] = "discord"

try:
    from utils.bootstrap_deps import ensure_discord_dependencies, ensure_news_dependencies, ensure_trader_dependencies
    ensure_discord_dependencies()
    ensure_news_dependencies()
    ensure_trader_dependencies()
except Exception as e:
    print(f"⚠️ bootstrap_deps failed (non-fatal): {e}", flush=True)

import asyncio
try:
    import discord
except ModuleNotFoundError as e:
    print("❌ Missing dependency: discord.py is not installed.", flush=True)
    print("Try:", flush=True)
    print(f"  {sys.executable} -m pip install -r requirements.txt", flush=True)
    print("or:", flush=True)
    print(f"  {sys.executable} -m pip install discord.py>=2.3.0", flush=True)
    sys.exit(1)
import signal
import time
import random
from discord import app_commands
from integrations import DISCORD_BOT_TOKEN
from config import (
    get_config,
    get_startup_channel_id,
    get_wake_word,
    is_bot_awake,
    get_conversation_channels,
    get_conversation_frequency,
)
from whitelist import get_user_permission
from conversations import conversation_manager
from services.reminder_service import reminder_manager
from services.news_service import news_manager
from platforms.discord_chat import process_discord_message
from commands.shitpost import handle_shitpost
from utils.llm_service import initialize_command_database
from utils import home_log
from utils import reliability_telemetry
from commands.shared import send_long_to_channel, bot_embed_thumbnail_url, sanitize_discord_bot_content
from adaptive_dm import adaptive_dm_manager, is_adaptive_context_export_filename

# Initialize command database
initialize_command_database()

# Initialize intents and client
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.reactions = True

class BotClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.start_time = time.time()
        self.commands_registered = False
        self._trader_webhook_server = None

    async def close(self):
        try:
            from services.clone_service import revert_if_active
            await revert_if_active(self)
        except Exception:
            pass
        try:
            from adaptive_dm import export_adaptive_to_personas
            from personas import persona_manager as _persona_manager

            export_adaptive_to_personas(_persona_manager)
        except Exception:
            pass
        conversation_manager.save()
        reminder_manager.stop()
        news_manager.stop()
        try:
            from utils.dm_image_flow_temp import clear_all_temp_sessions_sync

            clear_all_temp_sessions_sync()
        except Exception:
            pass
        try:
            if self._trader_webhook_server is not None:
                await self._trader_webhook_server.stop()
                self._trader_webhook_server = None
        except Exception:
            pass
        await super().close()

    async def setup_hook(self):
        """Setup hook to sync commands"""
        # Only register commands if not already registered
        if not self.commands_registered:
            await self.register_all_commands()
            self.commands_registered = True
            
        # Set reminder service client
        reminder_manager.set_client(self)

        # Set news service client
        news_manager.set_client(self)

        try:
            from adaptive_dm import export_adaptive_to_personas
            from personas import persona_manager as _persona_manager

            export_adaptive_to_personas(_persona_manager)
        except Exception:
            pass

        try:
            from integrations import TRADER_WEBHOOK_PORT
            from services.trader_webhook import TraderWebhookServer

            if TRADER_WEBHOOK_PORT and TRADER_WEBHOOK_PORT > 0:
                self._trader_webhook_server = TraderWebhookServer(self, TRADER_WEBHOOK_PORT)
                await self._trader_webhook_server.start()
        except Exception as e:
            print(f"⚠️ Trader webhook server failed to start: {e}", flush=True)

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
        # 4b. Calendar (.ics batch)
        try:
            from commands.cal import CalCommands
            CalCommands(self).register()
        except Exception as e:
            errors.append(f"cal: {e}")
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
        # 10. News
        try:
            from commands.news import NewsCommands
            NewsCommands(self).register()
        except Exception as e:
            errors.append(f"news: {e}")

        try:
            await self.tree.sync()
        except Exception as e:
            errors.append(f"tree.sync: {e}")
            print(f"⚠️ tree.sync() failed: {e}", flush=True)
        self._startup_errors = errors

client = BotClient()


@client.tree.interaction_check
async def _awake_interaction_gate(interaction: discord.Interaction) -> bool:
    """When sleeping, only allow /wake."""
    command_name = getattr(interaction.command, "name", "")
    if is_bot_awake() or command_name == "wake":
        return True
    if interaction.response.is_done():
        await interaction.followup.send("😴 I am sleeping. Use `/wake` to bring me online.", ephemeral=True)
    else:
        await interaction.response.send_message("😴 I am sleeping. Use `/wake` to bring me online.", ephemeral=True)
    return False

# In-memory counters for auto-conversation per channel: {channel_id: {"count": int, "next": int}}
_auto_conversation_state = {}

# Event handlers
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

    from integrations import refresh_environment_location_async
    from utils.bot_overview_embed import build_bot_overview_embed

    await refresh_environment_location_async()
    _errors, embed = await build_bot_overview_embed(client)

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

    if not is_bot_awake():
        return

    if message.guild:
        ag_cid = message.channel.id
        ag_aid = message.author.id
        ag_content = message.content

        async def _guild_adaptive_tune():
            try:
                await asyncio.to_thread(
                    adaptive_dm_manager.maybe_tune_from_guild_channel_message,
                    ag_cid,
                    ag_aid,
                    ag_content,
                )
            except Exception:
                pass

        asyncio.create_task(_guild_adaptive_tune())

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
                if is_adaptive_context_export_filename(attachment.filename):
                    continue
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

    # Passive auto-conversation in configured channels
    await _handle_auto_conversation(message)

    # Process Discord-specific chat (original functionality)
    try:
        handled = await process_discord_message(client, message, permission, conversation_manager)
    except Exception as e:
        handled = True
        error_count = reliability_telemetry.increment("message_handler_errors")
        try:
            await message.reply("⚠️ I hit an unexpected error while handling your message. Please retry.")
        except Exception:
            pass
        await home_log.send_to_home(
            f"🔴 process_discord_message failed (error #{error_count}): {str(e)[:500]}. "
            f"channel={getattr(message.channel, 'id', '?')} user={getattr(message.author, 'id', '?')}. "
            f"{reliability_telemetry.format_snapshot('Counters')}"
        )
    if handled:
        return

    # Shitpost fallback: only when no command/chat path handled the message.
    if await handle_shitpost(client, message):
        return


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Global admin: configured emoji deletes messages (guild: any; DM: bot-only)."""
    try:
        from services.remover_service import handle_raw_reaction_add

        await handle_raw_reaction_add(client, payload)
    except Exception as exc:
        await home_log.send_to_home(f"⚠️ on_raw_reaction_add: {str(exc)[:220]}")


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
        await message.channel.send(sanitize_discord_bot_content(answer))
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
        news_manager.stop()
        sys.exit(0)
        return
    if not client.is_closed():
        loop.create_task(client.close())
    else:
        conversation_manager.save()
        reminder_manager.stop()
        news_manager.stop()
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
    print(f"[startup] Python {sys.version}", flush=True)
    print(f"[startup] cwd = {os.getcwd()}", flush=True)
    print(f"[startup] discord.py {discord.__version__}", flush=True)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    for d in ("data", "data/files", "assets", "web"):
        os.makedirs(d, exist_ok=True)

    try:
        from services.status_server import start_status_server
        start_status_server()
    except Exception as e:
        print(f"⚠️ Status server failed: {e}", flush=True)

    _ensure_ollama_running()
    reminder_manager.start()
    news_manager.start()

    print("[startup] Connecting to Discord...", flush=True)
    try:
        client.run(DISCORD_BOT_TOKEN)
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
        conversation_manager.save()
        reminder_manager.stop()
        news_manager.stop()
        sys.exit(0)
    except discord.errors.LoginFailure:
        print("❌ Invalid Discord token. Check your DISCORD_BOT_TOKEN in integrations.py",
              flush=True)
        reminder_manager.stop()
        news_manager.stop()
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error starting bot: {e}", flush=True)
        import traceback
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        reminder_manager.stop()
        news_manager.stop()
        sys.exit(1)
