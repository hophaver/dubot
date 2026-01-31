from typing import Optional
import discord
from whitelist import get_user_permission
from utils.llm_service import analyze_file, FileProcessor
from commands.shared import send_long_message

MAX_FILE_BYTES = 8 * 1024 * 1024


async def run_image_command(
    interaction: discord.Interaction,
    file: discord.Attachment,
    prompt: Optional[str],
    vision_mode: str,
    default_prompt: str,
):
    if not get_user_permission(interaction.user.id):
        await interaction.response.send_message("❌ Denied", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        file_data = await file.read()
        if len(file_data) > MAX_FILE_BYTES:
            await interaction.followup.send("⚠️ File too large (max 8MB).")
            return
        if FileProcessor.get_file_type(file.filename) != "image":
            await interaction.followup.send("⚠️ Please upload an image.")
            return
        result = await analyze_file(
            interaction.user.id, interaction.channel.id, file.filename, file_data,
            prompt or default_prompt, str(interaction.user.name), vision_mode=vision_mode,
        )
        await send_long_message(interaction, result)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
