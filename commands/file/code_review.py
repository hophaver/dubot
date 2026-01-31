from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from utils.llm_service import analyze_file, FileProcessor
from commands.shared import send_long_message
from ._shared import MAX_FILE_BYTES


def register(client: discord.Client):
    @client.tree.command(name="code-review", description="Review and analyze code files")
    @app_commands.describe(file="Code file to review", focus_areas="What to focus on (e.g., security, performance)")
    async def code_review(interaction: discord.Interaction, file: discord.Attachment, focus_areas: Optional[str] = ""):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            file_data = await file.read()
            if len(file_data) > MAX_FILE_BYTES:
                await interaction.followup.send("⚠️ File too large (max 8MB).")
                return
            if FileProcessor.get_file_type(file.filename) != "code":
                await interaction.followup.send("⚠️ Please upload a code file (.py, .js, .java, etc.)")
                return
            focus_text = f" Focus on: {focus_areas}." if focus_areas else ""
            prompt = f"Review this code file.{focus_text} Check for: bugs, security, performance, style, best practices. Provide specific suggestions."
            result = await analyze_file(
                interaction.user.id, interaction.channel.id, file.filename, file_data,
                prompt, str(interaction.user.name),
            )
            await send_long_message(interaction, result)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
