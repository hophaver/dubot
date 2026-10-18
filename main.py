import os
import sys
import asyncio
import random
import signal
import time

try:
    import discord
except ModuleNotFoundError:
    print("❌ Missing dependency: discord.py is not installed.", flush=True)
    print(f"  Run: {sys.executable} -m pip install -r requirements.txt", flush=True)
    sys.exit(1)
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
from models import model_manager
from services.reminder_service import reminder_manager
from services.news_service import news_manager
from platforms.discord_chat import process_discord_message, handle_dm_user_typing
from commands.shitpost.trigger import handle_shitpost
from utils.llm.command_db import command_db
from utils import dm_background, home_log, http, reliability_telemetry
from utils.paths import root_path
from commands.shared import send_long_to_channel, sanitize_discord_bot_content
from adaptive_dm import adaptive_dm_manager, is_adaptive_context_export_filename
from services import notes_service

# Initialize intents and client
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.reactions = True
intents.typing = True

_SLEEP_REPLY = "😴 I am sleeping. Use `/wake` to bring me online."
_BACKGROUND_WAIT_ON_SHUTDOWN = 4.0  # stop.sh sends SIGKILL after ~5s


async def _ensure_ollama_running() -> None:
    """If config says start_ollama_on_startup and Ollama is not responding, start ollama serve in background."""
    if not get_config().get("start_ollama_on_startup"):
        return
    from utils.ollama import check_ollama_running_async, start_ollama_async

    if not await check_ollama_running_async():
        ok, msg = await start_ollama_async()
        print(("✅ " if ok else "⚠️ ") + msg, flush=True)


class BotCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """When sleeping, only allow /wake."""
        if is_bot_awake() or getattr(interaction.command, "name", "") == "wake":
            return True
        if interaction.response.is_done():
            await interaction.followup.send(_SLEEP_REPLY, ephemeral=True)
        else:
            await interaction.response.send_message(_SLEEP_REPLY, ephemeral=True)
        return False


class BotClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = BotCommandTree(self)
        self.start_time = time.time()
        self._startup_errors: list = []
        self._ready_once = False

    async def persist_state(self) -> None:
        """Flush everything that lives in memory to disk (shutdown, restart)."""
        try:
            from adaptive_dm import export_adaptive_to_personas
            from personas import persona_manager

            export_adaptive_to_personas(persona_manager)
        except Exception as e:
            print(f"⚠️ Adaptive export failed: {e}", flush=True)
        adaptive_dm_manager.flush()
        conversation_manager.flush()
        reminder_manager.save()

    async def close(self):
        if self.is_closed() or getattr(self, "_closing", False):
            return
        self._closing = True
        await self.persist_state()
        await dm_background.wait_all(timeout=_BACKGROUND_WAIT_ON_SHUTDOWN)
        try:
            from services.clone_service import revert_if_active

            await revert_if_active(self)
        except Exception as e:
            print(f"⚠️ Clone revert failed: {e}", flush=True)
        await reminder_manager.stop()
        await news_manager.stop()
        # Background summaries may have updated state while we waited.
        await self.persist_state()
        await http.close()
        await super().close()

    async def setup_hook(self):
        await self.register_all_commands()
        from commands.admin.update_shared import build_update_action_view

        self.add_view(build_update_action_view())  # update/rollback buttons survive restarts
        try:
            self.loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.close()))
        except (NotImplementedError, RuntimeError):
            pass  # Windows
        await _ensure_ollama_running()
        await model_manager.refresh_async()
        reminder_manager.start(self)
        news_manager.start(self)
        try:
            from adaptive_dm import export_adaptive_to_personas
            from personas import persona_manager

            export_adaptive_to_personas(persona_manager)
        except Exception as e:
            print(f"⚠️ Adaptive export failed: {e}", flush=True)

    async def register_all_commands(self):
        from commands import register_all

        self.tree.clear_commands(guild=None)
        errors, catalogue = register_all(self)
        command_db.populate(catalogue)
        print(f"✅ Registered {len(catalogue)} commands", flush=True)
        try:
            await self.tree.sync()
        except Exception as e:
            errors.append(f"tree.sync: {e}")
            print(f"⚠️ tree.sync() failed: {e}", flush=True)
        self._startup_errors = errors


client = BotClient()

# In-memory counters for auto-conversation per channel: {channel_id: {"count": int, "next": int}}
_auto_conversation_state = {}

# Event handlers
@client.event
async def on_ready():
    """Called when bot is ready (also after every reconnect). Startup work runs only the first time."""
    home_log.set_client(client)
    status_text = get_config().get("bot_status", "Analyzing files with AI")
    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=status_text))
    if client._ready_once:
        return
    client._ready_once = True
    from services.clone_service import on_bot_ready_baseline

    await on_bot_ready_baseline(client)
    notes_service.start_idle_loop(client)

    from integrations import refresh_environment_location_async
    from utils.bot_overview_embed import build_bot_overview_embed

    await refresh_environment_location_async()
    _errors, embed = await build_bot_overview_embed(client)

    sent = await home_log.send_to_home(embed=embed)
    if get_startup_channel_id() and not sent:
        await home_log.log("⚠️ Could not send startup message to home channel (check permissions).", also_send=False)

@client.event
async def on_typing(channel, user, when):
    try:
        await handle_dm_user_typing(channel, user)
    except Exception:
        pass


@client.event
async def on_message(message):
    """Handle incoming messages with file attachments"""
    # Ignore ourselves and other bots for everything below
    if message.author.bot:
        return

    from services.clone_service import mirror_message_if_clone
    if await mirror_message_if_clone(client, message):
        return

    # Note taker counts every human message in tracked channels (not just whitelisted users)
    if message.guild:
        try:
            notes_service.on_guild_message(client, message)
        except Exception as e:
            print(f"⚠️ notes on_guild_message failed: {e}", flush=True)

    # Get permission level
    permission = get_user_permission(message.author.id)
    if permission is None:
        return

    if not is_bot_awake():
        return

    if message.guild:
        # Pure regex work; runs on the loop so adaptive state is only mutated from one thread.
        try:
            adaptive_dm_manager.maybe_tune_from_guild_channel_message(
                message.channel.id, message.author.id, message.content
            )
        except Exception as e:
            print(f"⚠️ Guild adaptive tune failed: {e}", flush=True)

        if message.reference:
            try:
                if await notes_service.try_handle_notes_reply(client, message):
                    return
            except Exception as e:
                try:
                    await message.reply(f"❌ Notes edit failed: {str(e)[:300]}")
                except Exception:
                    pass
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

if __name__ == "__main__":
    print(f"[startup] Python {sys.version}", flush=True)
    print(f"[startup] discord.py {discord.__version__}", flush=True)

    for d in ("data", "assets", "web"):
        os.makedirs(root_path(d), exist_ok=True)

    try:
        from services.status_server import start_status_server

        start_status_server(client)
    except Exception as e:
        print(f"⚠️ Status server failed: {e}", flush=True)

    print("[startup] Connecting to Discord...", flush=True)
    try:
        # client.run handles Ctrl+C by calling close(); SIGTERM is wired in setup_hook.
        client.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("❌ Invalid Discord token. Check DISCORD_BOT_TOKEN in .env", flush=True)
        sys.exit(1)
    except Exception as e:
        import traceback

        print(f"❌ Error starting bot: {e}", flush=True)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)
