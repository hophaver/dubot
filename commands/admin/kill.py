import asyncio
import discord
from commands.shared import require_admin


def register(client: discord.Client):
    @client.tree.command(name="kill", description="Kill the bot")
    async def kill(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.send_message("💀 Shutting down bot...")

        async def _delayed_exit():
            await asyncio.sleep(1.5)
            await client.close()

        # Keep a reference so the task can't be garbage-collected before it runs.
        client._kill_task = asyncio.create_task(_delayed_exit())
