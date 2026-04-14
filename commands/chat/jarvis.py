import discord
from discord import app_commands

from jarvis import jarvis_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(name="jarvis", description="DM only: toggle adaptive personal assistant for this DM")
    @app_commands.describe(enabled="Turn adaptive assistant on or off for this DM")
    async def jarvis(interaction: discord.Interaction, enabled: bool):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ This command works only in DMs.", ephemeral=True)
            return
        jarvis_manager.set_enabled(interaction.user.id, enabled)
        if enabled:
            await interaction.response.send_message(
                "✅ Adaptive assistant enabled for this DM.\n"
                "I will learn your style/preferences and can run bot commands from natural language with confirmation."
            )
        else:
            jarvis_manager.clear_pending_confirmation(interaction.user.id)
            await interaction.response.send_message("✅ Adaptive assistant disabled for this DM.")
