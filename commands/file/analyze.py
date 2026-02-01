from typing import Optional
import discord
from discord import app_commands
from utils.llm_service import analyze_file
from commands.shared import send_long_message
from ._shared import defer_and_read_file


def register(client: discord.Client):
    @client.tree.command(name="analyze", description="Analyze uploaded files (images, text, code, documents)")
    @app_commands.describe(file="File to analyze", prompt="Custom analysis instructions (optional)")
    async def analyze(interaction: discord.Interaction, file: discord.Attachment, prompt: Optional[str] = ""):
        file_data = await defer_and_read_file(interaction, file)
        if file_data is None:
            return
        try:
            result = await analyze_file(
                interaction.user.id, interaction.channel.id, file.filename, file_data,
                prompt, str(interaction.user.name), vision_mode="concise",
            )
            await send_long_message(interaction, result)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
