import discord
from discord import app_commands
from whitelist import get_user_permission
from config import get_config


def register(client):
    @client.tree.command(name="checkwake", description="Check current wake word")
    async def checkwake(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        config = get_config()
        wake_word = config.get("wake_word", "dubot")
        embed = discord.Embed(title="🔔 Wake Word", color=discord.Color.blue())
        embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
        embed.add_field(name="Current", value=f"`{wake_word}`", inline=True)
        embed.add_field(name="Usage", value=f"Mention me or start with **{wake_word}** to chat.", inline=True)
        embed.set_footer(text="/setwake [Admin]")
        await interaction.response.send_message(embed=embed)
