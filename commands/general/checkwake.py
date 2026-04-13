import discord
from discord import app_commands
from whitelist import get_user_permission
from commands.shared import bot_embed_thumbnail_url
from config import get_wake_word


def register(client):
    @client.tree.command(name="checkwake", description="Check current wake word")
    async def checkwake(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        wake_word = get_wake_word()
        embed = discord.Embed(title="🔔 Wake Word", color=discord.Color.blue())
        _thumb = bot_embed_thumbnail_url(client.user)
        if _thumb:
            embed.set_thumbnail(url=_thumb)
        embed.add_field(name="Current", value=f"`{wake_word}`", inline=True)
        embed.add_field(name="Usage", value=f"Mention me or start with **{wake_word}** to chat.", inline=True)
        embed.set_footer(text="/setwake [Admin]")
        await interaction.response.send_message(embed=embed)
