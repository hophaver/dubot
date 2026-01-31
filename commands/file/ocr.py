from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from utils.llm_service import analyze_file, FileProcessor
from commands.shared import send_long_message
from ._shared import MAX_FILE_BYTES


def register(client: discord.Client):
    @client.tree.command(name="ocr", description="Extract text from images or documents (output is text only)")
    @app_commands.describe(file="Image or document file", language="Language hint (eng, spa, fra, deu, etc.)")
    async def ocr(interaction: discord.Interaction, file: discord.Attachment, language: Optional[str] = "eng"):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            file_data = await file.read()
            if len(file_data) > MAX_FILE_BYTES:
                await interaction.followup.send("⚠️ File too large (max 8MB).")
                return
            if FileProcessor.get_file_type(file.filename) not in ["image", "document"]:
                await interaction.followup.send("⚠️ Please upload an image or document file.")
                return
            prompt = f"Extract ALL text from this file. Be thorough and accurate. Language: {language}. Output only the extracted text, nothing else. Preserve line breaks and structure. If there's no text, output: No text found."
            result = await analyze_file(
                interaction.user.id, interaction.channel.id, file.filename, file_data,
                prompt, str(interaction.user.name), vision_mode="concise", return_only_text=True,
            )
            await send_long_message(interaction, result)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
