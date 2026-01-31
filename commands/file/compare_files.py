from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from utils.llm_service import compare_files
from commands.shared import send_long_message


def register(client: discord.Client):
    @client.tree.command(name="compare-files", description="Compare two or more text files")
    @app_commands.describe(file1="First file", file2="Second file", file3="Optional", file4="Optional", prompt="Instructions (optional)")
    async def compare_files_cmd(
        interaction: discord.Interaction,
        file1: discord.Attachment,
        file2: discord.Attachment,
        file3: Optional[discord.Attachment] = None,
        file4: Optional[discord.Attachment] = None,
        prompt: Optional[str] = "",
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            files = [f for f in (file1, file2, file3, file4) if f is not None]
            if len(files) < 2:
                await interaction.followup.send("⚠️ Upload at least 2 files to compare.")
                return
            file_data_list = [{"filename": f.filename, "data": await f.read()} for f in files]
            result = await compare_files(
                interaction.user.id, interaction.channel.id, file_data_list, prompt, str(interaction.user.name)
            )
            await send_long_message(interaction, result)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
