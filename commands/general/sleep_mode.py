import discord
from discord import app_commands
from whitelist import get_user_permission
from config import set_bot_awake


def register(client: discord.Client):
    @client.tree.command(name="sleep", description="Put bot offline until /wake")
    async def sleep(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        set_bot_awake(False)
        await interaction.response.send_message("😴 Going offline. I will ignore everything except `/wake`.")

    @client.tree.command(name="wake", description="Bring bot back online")
    async def wake(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        set_bot_awake(True)
        await interaction.response.send_message("✅ Awake and back online.")
