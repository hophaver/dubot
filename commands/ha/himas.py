from commands.shared import require_himas
import discord
from discord import app_commands


def register(client: discord.Client):
    @client.tree.command(name="himas", description="Control Home Assistant with natural language")
    @app_commands.describe(command="Natural language command")
    async def himas(interaction: discord.Interaction, command: str):
        if not await require_himas(interaction):
            return
        await interaction.response.defer()
        try:
            from utils.ha_integration import ask_home_assistant_meta
            response, parse_footer = await ask_home_assistant_meta(command, interaction.user.id)
            if not response or not response.strip():
                response = "No response. Check your command or use /listentities to see available devices."
            embed = discord.Embed(title="🏠 Home Assistant", description=response[:4096], color=discord.Color.blue())
            embed.set_footer(text=f"Control: {parse_footer}"[:2048])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
