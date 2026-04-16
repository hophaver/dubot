import discord
from discord import app_commands

from adaptive_dm import adaptive_dm_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(name="adaptive-tune", description="DMs: apply queued messages to your adaptive profile now")
    @app_commands.describe(force="Run even with fewer queued messages")
    async def adaptive_tune(interaction: discord.Interaction, force: bool = False):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ DMs only.", ephemeral=True)
            return
        label = (getattr(interaction.user, "global_name", None) or interaction.user.name or "").strip()
        adaptive_dm_manager.touch_adaptive_sync_display_name(interaction.user.id, label)
        if not adaptive_dm_manager.is_enabled(interaction.user.id):
            await interaction.response.send_message(
                "Turn **adaptive** on first (`/adaptive` → enabled: on).",
                ephemeral=True,
            )
            return

        updated = adaptive_dm_manager.run_tone_tuning_now(interaction.user.id, force=force)
        if updated:
            await interaction.response.send_message("✅ Profile updated from your recent messages.")
        else:
            await interaction.response.send_message(
                "Not enough queued messages yet — try again later or use **force: true**.",
                ephemeral=True,
            )
