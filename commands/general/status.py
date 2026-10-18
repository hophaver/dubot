import time
import discord
from commands.shared import require_user
from models import model_manager
from services.status_server import STATUS_JSON_PATH
from utils.json_store import save_json
from utils.llm_service import command_db, SUPPORTED_FILE_TYPES
from utils.system_monitor import format_uptime, get_system_status_async


def register(client):
    @client.tree.command(name="status", description="Show system status and bot info")
    async def status(interaction: discord.Interaction):
        if not await require_user(interaction):
            return
        await interaction.response.defer()
        from services.clone_service import sync_identity
        from integrations import refresh_environment_location_async
        from utils.bot_overview_embed import build_bot_overview_embed

        await sync_identity(client, interaction.guild)
        await refresh_environment_location_async()

        sys_status = await get_system_status_async()
        if "error" in sys_status:
            await interaction.followup.send(f"❌ Error getting status: {sys_status['error']}")
            return

        bot_uptime_sec = time.time() - client.start_time
        model_info = model_manager.get_user_model_info(interaction.user.id)
        save_json(
            STATUS_JSON_PATH,
            {
                "system": sys_status,
                "bot_uptime_sec": round(bot_uptime_sec, 1),
                "bot_uptime": format_uptime(bot_uptime_sec),
                "model": f"{model_info.get('model', 'default')} ({model_info.get('provider', 'local')})",
                "basic_local_model": model_manager.get_last_local_model(interaction.user.id, refresh_local=True),
                "commands_count": len(command_db.commands),
                "file_types_count": len(SUPPORTED_FILE_TYPES),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )

        _errs, embed = await build_bot_overview_embed(
            client,
            errors=[],
            include_hardware=True,
            sys_status=sys_status,
        )
        await interaction.followup.send(embed=embed)
