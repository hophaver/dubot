import discord
from conversations import conversation_manager
from commands.shared import require_admin


def register(client: discord.Client):
    @client.tree.command(name="forget", description="Clear your chat history")
    async def forget(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        conversation_manager.clear_conversation(interaction.channel.id)
        await interaction.response.send_message("✅ Chat history cleared.")
