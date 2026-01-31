from typing import Optional
import discord
from discord import app_commands
from ._shared import run_image_command


def register(client: discord.Client):
    @client.tree.command(name="examine", description="Detailed image analysis (full description)")
    @app_commands.describe(file="Image file", prompt="Optional instructions")
    async def examine(interaction: discord.Interaction, file: discord.Attachment, prompt: Optional[str] = ""):
        await run_image_command(interaction, file, prompt, "examine", "Describe this image in full detail.")
