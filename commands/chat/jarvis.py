import discord
from discord import app_commands

from jarvis import jarvis_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(name="jarvis", description="DM only: toggle adaptive Jarvis assistant mode")
    @app_commands.describe(enabled="Turn Jarvis mode on or off")
    async def jarvis(interaction: discord.Interaction, enabled: bool):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ `/jarvis` works only in DMs.", ephemeral=True)
            return
        jarvis_manager.set_enabled(interaction.user.id, enabled)
        if enabled:
            await interaction.response.send_message(
                "✅ Jarvis mode enabled for this DM.\n"
                "I will learn your style/preferences and can execute bot commands from natural language with confirmation."
            )
        else:
            jarvis_manager.clear_pending_confirmation(interaction.user.id)
            await interaction.response.send_message("✅ Jarvis mode disabled for this DM.")
