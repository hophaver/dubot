import discord
from commands.shared import make_bot_embed, require_user
from config import get_wake_word


def register(client):
    @client.tree.command(name="checkwake", description="Check current wake word")
    async def checkwake(interaction: discord.Interaction):
        if not await require_user(interaction):
            return
        wake_word = get_wake_word()
        embed = make_bot_embed(client, "🔔 Wake Word", color=discord.Color.blue())
        embed.add_field(name="Current", value=f"`{wake_word}`", inline=True)
        embed.add_field(name="Usage", value=f"Mention me or start with **{wake_word}** to chat.", inline=True)
        embed.set_footer(text="/setwake [Admin]")
        await interaction.response.send_message(embed=embed)
