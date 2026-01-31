from typing import Optional
import discord
from discord import app_commands
from ._shared import run_image_command


def register(client: discord.Client):
    @client.tree.command(name="interrogate", description="Short image answer (few sentences or bullets)")
    @app_commands.describe(file="Image file", prompt="Optional question")
    async def interrogate(interaction: discord.Interaction, file: discord.Attachment, prompt: Optional[str] = ""):
        await run_image_command(
            interaction, file, prompt, "interrogate",
            "What do you see? Answer in a few short sentences or bullet points.",
        )
