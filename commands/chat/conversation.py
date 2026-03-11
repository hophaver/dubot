from typing import Optional

import discord
from discord import app_commands

from whitelist import is_admin, get_user_permission
from config import get_conversation_channels, add_conversation_channel, remove_conversation_channel


def register(client: discord.Client):
    @client.tree.command(
        name="conversation",
        description="Enable or disable auto-conversation in a channel",
    )
    @app_commands.describe(
        enabled="Set to true to enable, false to disable",
        channel="Channel to configure (default: current channel)",
    )
    async def conversation(
        interaction: discord.Interaction,
        enabled: bool,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("❌ Conversation can only be enabled for text channels.", ephemeral=True)
            return

        current = get_conversation_channels()
        if enabled:
            add_conversation_channel(target_channel.id)
            msg = f"✅ Auto-conversation **enabled** for {target_channel.mention}."
        else:
            remove_conversation_channel(target_channel.id)
            msg = f"✅ Auto-conversation **disabled** for {target_channel.mention}."

        # Show summary of channels
        updated = get_conversation_channels()
        if updated:
            channel_list = ", ".join(f"<#{cid}>" for cid in updated)
            msg += f"\n\nCurrently enabled channels: {channel_list}"
        else:
            msg += "\n\nNo channels currently have auto-conversation enabled."

        await interaction.response.send_message(msg)

