import discord
import asyncio
import subprocess
import sys
import os
import signal
import time
from discord import app_commands
from integrations import DISCORD_BOT_TOKEN
from config import get_config
from whitelist import get_user_permission
from conversations import conversation_manager
from services.reminder_service import reminder_manager
from platforms.discord_chat import process_discord_message
from utils.llm_service import initialize_command_database

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
        except ImportError:
            await self._register_basic_help()
        except Exception as e:
            errors.append(f"help: {e}")
            await self._register_basic_help()

        await self.tree.sync()
        self._startup_errors = errors
    
    async def _register_basic_help(self):
        """Register basic help command if help.py is missing"""
        @self.tree.command(name="help", description="Show all available commands")
        async def help_command(interaction: discord.Interaction):
            if not get_user_permission(interaction.user.id):
                await interaction.response.send_message("❌ Denied", ephemeral=True)
                return
            
            embed = discord.Embed(title="📖 Help", color=discord.Color.blue())
            embed.set_thumbnail(url=self.user.display_avatar.url if self.user else None)
            embed.add_field(name="💬 General", value="`/chat` `/forget` `/status` `/checkwake` `/translate` `/help`", inline=True)
            embed.add_field(name="📄 File", value="`/analyze` `/examine` `/interrogate` `/code-review` `/ocr` `/compare-files`", inline=True)
            embed.add_field(name="⏰ Reminders", value="`/remind` `/reminders` `/cancel-reminder`", inline=True)
            embed.add_field(name="🎭 Persona", value="`/persona` `/persona-create`", inline=True)
            embed.add_field(name="🤖 Model", value="`/model` `/pull-model`", inline=True)
            embed.add_field(name="📥 Download", value="`/download` `/download-limit`", inline=True)
            embed.add_field(name="📜 Scripts", value="`/scripts` `/run`", inline=True)
            embed.add_field(name="🔧 Admin", value="`/whitelist` `/setwake` `/sethome` `/setstatus` `/purge` `/restart` `/kill` `/update`", inline=True)
            embed.add_field(name="🏠 Home Assistant", value="`/himas` `/explain` `/listentities` `/removeentity` `/ha-status` `/find-sensor`", inline=True)
            embed.set_footer(text="/help [command]")
            await interaction.response.send_message(embed=embed)

client = BotClient()

# Event handlers
@client.event
async def on_ready():
    """Called when bot is ready"""
    config = get_config()
    status_text = config.get("bot_status", "Analyzing files with AI")
    activity = discord.Activity(type=discord.ActivityType.listening, name=status_text)
    await client.change_presence(activity=activity)

    errors = getattr(client, "_startup_errors", [])
    if errors:
        print("⚠️ Startup completed with errors:", ", ".join(errors))
    else:
        print("✅ Startup OK")

    # Check Home Assistant and default model for startup message
    ha_status = "Not configured"
    try:
        from integrations import HA_URL, HA_ACCESS_TOKEN
        if HA_URL and HA_URL.strip() and HA_ACCESS_TOKEN:
            from utils.ha_integration import ha_manager
            entities = await ha_manager.get_all_entities()
            ha_status = "Connected" if entities else "Disconnected"
        elif not HA_ACCESS_TOKEN:
            ha_status = "Not configured"
    except Exception:
        ha_status = "Disconnected"

    from models import model_manager
    model_info = model_manager.get_user_model_info(0)
    current_model = f"{model_info.get('model', 'qwen2.5:7b')} ({model_info.get('provider', 'local')})"

    channel_id = config.get("startup_channel_id")
    if channel_id:
        try:
            channel = client.get_channel(int(channel_id))
            if channel:
                embed = discord.Embed(
                    title="🤖 Startup" if not errors else "🤖 Startup (with issues)",
                    color=discord.Color.green() if not errors else discord.Color.orange(),
                )
                embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
                embed.add_field(name="Status", value="OK" if not errors else "Errors: " + ", ".join(errors)[:500], inline=True)
                embed.add_field(name="Model", value=current_model[:100], inline=True)
                embed.add_field(name="Home Assistant", value=ha_status, inline=True)
                embed.set_footer(text=time.strftime("%Y-%m-%d %H:%M:%S"))
                await channel.send(embed=embed)
        except discord.errors.Forbidden:
            print("⚠️ Missing permissions in startup channel")
        except Exception as e:
            print("⚠️ Startup message:", e)

@client.event
async def on_message(message):
    """Handle incoming messages with file attachments"""
    if message.author == client.user:
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
                
                # Send result in chunks if too long
                if len(result) > 1900:
                    chunks = [result[i:i+1900] for i in range(0, len(result), 1900)]
                    for chunk in chunks:
                        await message.channel.send(chunk)
                else:
                    await message.channel.send(result)
                    
            except Exception as e:
                await message.channel.send(f"❌ Error analyzing {attachment.filename}: {str(e)}")
        
        # Don't process further if we handled files
        return
    
    # Process Discord-specific chat (original functionality)
    await process_discord_message(client, message, permission, conversation_manager)

@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle slash command errors."""
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

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print("\n👋 Received shutdown signal, cleaning up...")
    # Save conversation data
    conversation_manager.save()
    # Stop reminder service
    reminder_manager.stop()
    # Exit gracefully
    sys.exit(0)

def _ensure_ollama_running():
    """If config says start_ollama_on_startup and Ollama is not responding, start ollama serve in background."""
    try:
        config = get_config()
        if not config.get("start_ollama_on_startup"):
            return
        import urllib.request
        from integrations import OLLAMA_URL
        base = OLLAMA_URL.rstrip("/")
        try:
            urllib.request.urlopen(f"{base}/api/tags", timeout=2)
            return  # Already running
        except Exception:
            pass
        # Start ollama serve in a new process so it doesn't block
        kwargs = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            ["ollama", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except Exception:
        pass


if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Ensure data directory exists
    os.makedirs("data", exist_ok=True)
    os.makedirs("data/files", exist_ok=True)
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
