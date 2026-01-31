import os
import sys
import discord
from whitelist import is_admin


def register(client: discord.Client):
    @client.tree.command(name="restart", description="Restart the bot")
    async def restart(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_message("🔄 Restarting bot...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
