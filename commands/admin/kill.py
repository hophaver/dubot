import asyncio
import discord
from whitelist import is_admin


def register(client: discord.Client):
    @client.tree.command(name="kill", description="Kill the bot")
    async def kill(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_message("💀 Shutting down bot...")

        async def _delayed_exit():
            await asyncio.sleep(1.5)
            await client.close()

        asyncio.create_task(_delayed_exit())
