from typing import Optional

import discord
from discord import app_commands

from whitelist import is_admin, get_user_permission
from config import get_conversation_frequency, set_conversation_frequency


def register(client: discord.Client):
    @client.tree.command(
        name="conversation-frequency",
        description="View or set how often the bot auto-replies in conversation channels",
    )
    @app_commands.describe(
        min_messages="Minimum number of messages between bot replies (omit to view current)",
        max_messages="Maximum number of messages between bot replies (omit to view current)",
    )
    async def conversation_frequency(
        interaction: discord.Interaction,
        min_messages: Optional[int] = None,
        max_messages: Optional[int] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        current_min, current_max = get_conversation_frequency()

        # View current settings
        if min_messages is None and max_messages is None:
            await interaction.response.send_message(
                f"Auto-conversation frequency: every **{current_min}–{current_max}** messages "
                f"(random within this range) in enabled channels.",
                ephemeral=True,
            )
            return

        # Only admins can change
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only to change.", ephemeral=True)
            return

        # If only one bound is provided, keep the other as-is
        if min_messages is None:
            min_messages = current_min
        if max_messages is None:
            max_messages = current_max

        if min_messages < 1 or max_messages < 1:
            await interaction.response.send_message("❌ Use numbers ≥ 1.", ephemeral=True)
            return

        if max_messages < min_messages:
            await interaction.response.send_message(
                "❌ `max_messages` must be greater than or equal to `min_messages`.",
                ephemeral=True,
            )
            return

        set_conversation_frequency(min_messages, max_messages)
        new_min, new_max = get_conversation_frequency()
        await interaction.response.send_message(
            f"✅ Auto-conversation frequency set to every **{new_min}–{new_max}** messages "
            f"(random within this range) in enabled channels.",
            ephemeral=True,
        )

