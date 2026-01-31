import asyncio
import sys
import discord
from whitelist import is_admin
from conversations import conversation_manager
from services.reminder_service import reminder_manager


def register(client: discord.Client):
    @client.tree.command(name="kill", description="Kill the bot")
    async def kill(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_message("💀 Shutting down bot...")

        async def _delayed_exit():
            await asyncio.sleep(1.5)
            conversation_manager.save()
            reminder_manager.stop()
            sys.exit(0)
        asyncio.create_task(_delayed_exit())
