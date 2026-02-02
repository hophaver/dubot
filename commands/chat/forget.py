import discord
from whitelist import is_admin
from conversations import conversation_manager


def register(client: discord.Client):
    @client.tree.command(name="forget", description="Clear your chat history")
    async def forget(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        conversation_manager.clear_conversation(interaction.channel.id)
        await interaction.response.send_message("✅ Chat history cleared.")
