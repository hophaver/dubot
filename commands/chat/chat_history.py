from typing import Optional
import discord
from discord import app_commands
from whitelist import is_admin, get_user_permission
from config import get_chat_history, set_chat_history
from conversations import conversation_manager


def register(client: discord.Client):
    @client.tree.command(name="chat-history", description="View or set how many user messages to remember per chat (1–100)")
    @app_commands.describe(number="Set to this number (omit to show current)")
    async def chat_history(interaction: discord.Interaction, number: Optional[int] = None):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        current = get_chat_history()
        if number is None:
            await interaction.response.send_message(
                f"Chat history: **{current}** user messages per chat (last {current} back-and-forths kept). Use `/chat-history <number>` to change (admin only).",
                ephemeral=True,
            )
            return
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only to change.", ephemeral=True)
            return
        if number < 1 or number > 100:
            await interaction.response.send_message("❌ Use a number between 1 and 100.", ephemeral=True)
            return
        set_chat_history(number)
        conversation_manager.set_max_history(number)
        await interaction.response.send_message(
            f"✅ Chat history set to **{number}** user messages per chat (last {number} back-and-forths kept).",
            ephemeral=True,
        )
