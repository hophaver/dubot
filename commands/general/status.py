import os
import json
import time
import discord
from discord import app_commands
from whitelist import get_user_permission
from config import get_config
from utils.system_monitor import get_system_status
from models import model_manager

STATUS_WEB_DIR = "web"
STATUS_JSON_PATH = os.path.join(STATUS_WEB_DIR, "status.json")


def _write_status_json(sys_status, bot_uptime_sec, bot_uptime, current_model, commands_count, file_types_count):
    os.makedirs(STATUS_WEB_DIR, exist_ok=True)
    payload = {
        "system": sys_status,
        "bot_uptime_sec": round(bot_uptime_sec, 1),
        "bot_uptime": bot_uptime,
        "model": current_model,
        "commands_count": commands_count,
        "file_types_count": file_types_count,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(STATUS_JSON_PATH, "w") as f:
        json.dump(payload, f, indent=2)


def register(client):
    @client.tree.command(name="status", description="Show system status and bot info")
    async def status(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from services.clone_service import sync_identity
            await sync_identity(client, interaction.guild)
            from utils.llm_service import command_db, SUPPORTED_FILE_TYPES
            sys_status = get_system_status()
            if "error" in sys_status:
                await interaction.followup.send(f"❌ Error getting status: {sys_status['error']}")
                return
            bot_uptime_sec = time.time() - client.start_time
            b_days = int(bot_uptime_sec // 86400)
            b_hours = int((bot_uptime_sec % 86400) // 3600)
            b_mins = int((bot_uptime_sec % 3600) // 60)
            bot_uptime = f"{b_days}d {b_hours}h {b_mins}m" if b_days else f"{b_hours}h {b_mins}m"
            model_info = model_manager.get_user_model_info(interaction.user.id)
            current_model = f"{model_info.get('model', 'default')} ({model_info.get('provider', 'local')})"
            commands_count = len(command_db.commands)
            file_types_count = len(SUPPORTED_FILE_TYPES)
            _write_status_json(sys_status, bot_uptime_sec, bot_uptime, current_model, commands_count, file_types_count)
            embed = discord.Embed(title="🤖 Status", color=discord.Color.blue())
            embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
            embed.add_field(name="🌐 IP", value=sys_status["ip_address"], inline=True)
            embed.add_field(name="🖥️ Host", value=sys_status["hostname"], inline=True)
            embed.add_field(name="📋 OS", value=sys_status["os"], inline=True)
            embed.add_field(name="⚡ CPU", value=f"{sys_status['cpu_percent']}%", inline=True)
            embed.add_field(name="🧠 RAM", value=f"{sys_status['memory_used']}/{sys_status['memory_total']} MB", inline=True)
            embed.add_field(name="💾 Disk", value=f"{sys_status['disk_used']}/{sys_status['disk_total']} GB", inline=True)
            embed.add_field(name="🎮 GPU", value=f"{sys_status.get('gpu_util', 'N/A')} · {sys_status['gpu_temp']}", inline=True)
            embed.add_field(name="⏱️ Bot uptime", value=bot_uptime, inline=True)
            embed.add_field(name="🔄 System uptime", value=sys_status["uptime"], inline=True)
            embed.add_field(name="🤖 Model", value=current_model[:100], inline=True)
            embed.add_field(name="📜 Commands", value=str(commands_count), inline=True)
            embed.add_field(name="📁 File types", value=str(file_types_count), inline=True)
            embed.set_footer(text=f"Python {sys_status['python_version']} · /help")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error getting status: {str(e)}")
