import discord
from discord import app_commands
from whitelist import is_admin
from config import get_config, save_config


def register(client: discord.Client):
    @client.tree.command(name="sethome", description="Set startup channel (startup, errors, and logs)")
    @app_commands.describe(channel="Channel for startup messages, errors, and logs")
    async def sethome(interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        config = get_config()
        config["startup_channel_id"] = str(channel.id)
        save_config(config)
        await interaction.response.send_message(f"✅ Startup channel set to: {channel.mention}")
