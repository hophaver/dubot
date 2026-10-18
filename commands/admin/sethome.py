import discord
from discord import app_commands
from config import get_config, save_config
from commands.shared import require_admin


def register(client: discord.Client):
    @client.tree.command(name="sethome", description="Set startup channel (startup, errors, and logs)")
    @app_commands.describe(channel="Channel for startup messages, errors, and logs")
    async def sethome(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await require_admin(interaction):
            return
        config = get_config()
        config["startup_channel_id"] = str(channel.id)
        save_config(config)
        await interaction.response.send_message(f"✅ Startup channel set to: {channel.mention}")
