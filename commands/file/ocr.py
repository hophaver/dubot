from typing import Optional
import discord
from discord import app_commands
from utils.llm_service import analyze_file
from commands.shared import send_long_message
from ._shared import defer_and_read_file


def register(client: discord.Client):
    @client.tree.command(name="ocr", description="Extract text from images or documents (output is text only)")
    @app_commands.describe(file="Image or document file", language="Language hint (eng, spa, fra, deu, etc.)")
    async def ocr(interaction: discord.Interaction, file: discord.Attachment, language: Optional[str] = "eng"):
        file_data = await defer_and_read_file(
            interaction, file,
            allowed_types=["image", "document"],
            type_error_msg="⚠️ Please upload an image or document file.",
        )
        if file_data is None:
            return
        try:
            prompt = f"Extract ALL text from this file. Be thorough and accurate. Language: {language}. Output only the extracted text, nothing else. Preserve line breaks and structure. If there's no text, output: No text found."
            result = await analyze_file(
                interaction.user.id, interaction.channel.id, file.filename, file_data,
                prompt, str(interaction.user.name), vision_mode="concise", return_only_text=True,
            )
            await send_long_message(interaction, result)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
