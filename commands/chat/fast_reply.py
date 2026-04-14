from typing import Optional

import discord
from discord import app_commands

from conversations import conversation_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(
        name="fast-reply",
        description="DM only: temporarily enable snappier, shorter replies",
    )
    @app_commands.describe(
        enabled="Enable or disable fast reply mode for this DM",
        minutes="Duration in minutes when enabling (default: 30, max: 240)",
    )
    async def fast_reply(
        interaction: discord.Interaction,
        enabled: bool,
        minutes: Optional[int] = 30,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message(
                "ℹ️ Fast reply is already ON by default in server channels.\n"
                "Use this command in DMs to enable it temporarily.",
                ephemeral=True,
            )
            return

        channel_id = interaction.channel.id
        if enabled:
            duration = max(1, min(240, int(minutes or 30)))
            conversation_manager.set_dm_fast_reply_window(channel_id, duration)
            conversation_manager.save()
            await interaction.response.send_message(
                f"✅ Fast reply enabled for this DM for about **{duration} minutes**."
            )
            return

        conversation_manager.clear_dm_fast_reply_window(channel_id)
        conversation_manager.save()
        await interaction.response.send_message("✅ Fast reply disabled for this DM.")
