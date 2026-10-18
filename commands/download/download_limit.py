import discord
from discord import app_commands
from config import set_download_limit_mb
from commands.shared import require_admin


def register(client: discord.Client):
    @client.tree.command(name="download-limit", description="[Admin] Set max download file size in MB")
    @app_commands.describe(mb="Max file size in MB (1–2000)")
    async def download_limit(interaction: discord.Interaction, mb: int):
        if not await require_admin(interaction):
            return
        mb = max(1, min(2000, mb))
        set_download_limit_mb(mb)
        await interaction.response.send_message(f"✅ Download limit set to **{mb}** MB.")
