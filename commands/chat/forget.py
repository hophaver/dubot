import discord
from whitelist import get_user_permission
from conversations import conversation_manager


def register(client: discord.Client):
    @client.tree.command(name="forget", description="Clear your chat history")
    async def forget(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        conversation_manager.clear_conversation(interaction.channel.id)
        await interaction.response.send_message("✅ Chat history cleared.")
