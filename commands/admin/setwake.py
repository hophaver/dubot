import discord
from discord import app_commands
from config import get_config, save_config
from commands.shared import require_admin


def register(client: discord.Client):
    @client.tree.command(name="setwake", description="Change wake word")
    @app_commands.describe(wake_word="New wake word")
    async def setwake(interaction: discord.Interaction, wake_word: str):
        if not await require_admin(interaction):
            return
        config = get_config()
        config["wake_word"] = wake_word
        save_config(config)
        await interaction.response.send_message(f"✅ Wake word changed to: `{wake_word}`")
