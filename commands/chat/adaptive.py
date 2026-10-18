import discord
from discord import app_commands

from adaptive_dm import adaptive_dm_manager
from commands.shared import require_user


def register(client: discord.Client):
    @client.tree.command(name="adaptive", description="DMs: turn adaptive assistant on or off")
    @app_commands.describe(enabled="On or off for this DM")
    async def adaptive(interaction: discord.Interaction, enabled: bool):
        if not await require_user(interaction):
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ DMs only.", ephemeral=True)
            return
        label = (getattr(interaction.user, "global_name", None) or interaction.user.name or "").strip()
        adaptive_dm_manager.touch_adaptive_sync_display_name(interaction.user.id, label)
        adaptive_dm_manager.set_enabled(interaction.user.id, enabled)
        if enabled:
            await interaction.response.send_message(
                "✅ **Adaptive** is on. I’ll match your style and can propose commands (with confirmation when needed)."
            )
        else:
            adaptive_dm_manager.clear_pending_confirmation(interaction.user.id)
            await interaction.response.send_message("✅ **Adaptive** is off for this DM.")
