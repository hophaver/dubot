import discord
from discord import app_commands

from jarvis import jarvis_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(name="jarvis-tune", description="DM only: manually run Jarvis tone tuning now")
    @app_commands.describe(force="Run tuning even with fewer queued messages")
    async def jarvis_tune(interaction: discord.Interaction, force: bool = False):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ `/jarvis-tune` works only in DMs.", ephemeral=True)
            return
        if not jarvis_manager.is_enabled(interaction.user.id):
            await interaction.response.send_message("ℹ️ Jarvis is disabled in this DM. Enable it with `/jarvis enabled:true`.", ephemeral=True)
            return

        updated = jarvis_manager.run_tone_tuning_now(interaction.user.id, force=force)
        if updated:
            await interaction.response.send_message("✅ Tone tuning updated from your queued messages.")
        else:
            await interaction.response.send_message(
                "ℹ️ Not enough new user messages yet for tuning.\n"
                "You can use `/jarvis-tune force:true` to run it anyway.",
                ephemeral=True,
            )
