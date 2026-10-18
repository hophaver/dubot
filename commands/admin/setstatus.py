import discord
from discord import app_commands
from config import get_config, save_config
from commands.shared import require_admin


def register(client: discord.Client):
    @client.tree.command(name="setstatus", description="Change bot status")
    @app_commands.describe(status="New status message")
    async def setstatus(interaction: discord.Interaction, status: str):
        if not await require_admin(interaction):
            return
        config = get_config()
        config["bot_status"] = status
        save_config(config)
        activity = discord.Activity(type=discord.ActivityType.listening, name=status)
        await interaction.client.change_presence(activity=activity)
        await interaction.response.send_message(f"✅ Bot status changed to: `{status}`")
