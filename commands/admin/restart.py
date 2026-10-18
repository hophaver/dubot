import discord
from commands.admin.update_shared import restart_bot
from commands.shared import require_admin


def register(client: discord.Client):
    @client.tree.command(name="restart", description="Restart the bot")
    async def restart(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.send_message("🔄 Restarting bot...")
        await restart_bot(client)
