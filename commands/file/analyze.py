from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from utils.llm_service import analyze_file
from commands.shared import send_long_message
from ._shared import MAX_FILE_BYTES


def register(client: discord.Client):
    @client.tree.command(name="analyze", description="Analyze uploaded files (images, text, code, documents)")
    @app_commands.describe(file="File to analyze", prompt="Custom analysis instructions (optional)")
    async def analyze(interaction: discord.Interaction, file: discord.Attachment, prompt: Optional[str] = ""):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            file_data = await file.read()
            if len(file_data) > MAX_FILE_BYTES:
                await interaction.followup.send("⚠️ File too large (max 8MB).")
                return
            result = await analyze_file(
                interaction.user.id, interaction.channel.id, file.filename, file_data,
                prompt, str(interaction.user.name), vision_mode="concise",
            )
            await send_long_message(interaction, result)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
