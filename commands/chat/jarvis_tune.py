import discord
from discord import app_commands

from jarvis import jarvis_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(name="jarvis-tune", description="DM only: manually update tone/preferences from queued messages")
    @app_commands.describe(force="Run tuning even with fewer queued messages")
    async def jarvis_tune(interaction: discord.Interaction, force: bool = False):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ This command works only in DMs.", ephemeral=True)
            return
        if not jarvis_manager.is_enabled(interaction.user.id):
            await interaction.response.send_message(
                "ℹ️ Adaptive assistant is off in this DM. Turn it on with the DM assistant toggle first.",
                ephemeral=True,
            )
            return
        if jarvis_manager.get_profile_manual_override(interaction.user.id):
            await interaction.response.send_message(
                "ℹ️ Manual user context is active. Clear it with **`reset manual`** as a reply to your latest status export message before auto tone tuning can update the profile.",
                ephemeral=True,
            )
            return

        updated = jarvis_manager.run_tone_tuning_now(interaction.user.id, force=force)
        if updated:
            await interaction.response.send_message("✅ Tone and preferences updated from your queued messages.")
        else:
            await interaction.response.send_message(
                "ℹ️ Not enough new user messages yet for tuning.\n"
                "You can run this command again with **force: true** to update anyway.",
                ephemeral=True,
            )
