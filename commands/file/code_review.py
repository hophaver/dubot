from typing import Optional
import discord
from discord import app_commands
from utils.llm_service import analyze_file
from commands.shared import send_long_message
from ._shared import defer_and_read_file


def register(client: discord.Client):
    @client.tree.command(name="code-review", description="Review and analyze code files")
    @app_commands.describe(file="Code file to review", focus_areas="What to focus on (e.g., security, performance)")
    async def code_review(interaction: discord.Interaction, file: discord.Attachment, focus_areas: Optional[str] = ""):
        file_data = await defer_and_read_file(
            interaction, file,
            allowed_types="code",
            type_error_msg="⚠️ Please upload a code file (.py, .js, .java, etc.)",
        )
        if file_data is None:
            return
        try:
            focus_text = f" Focus on: {focus_areas}." if focus_areas else ""
            prompt = f"Review this code file.{focus_text} Check for: bugs, security, performance, style, best practices. Provide specific suggestions."
            result = await analyze_file(
                interaction.user.id, interaction.channel.id, file.filename, file_data,
                prompt, str(interaction.user.name),
            )
            await send_long_message(interaction, result)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
