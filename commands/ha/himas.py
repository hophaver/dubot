import discord
from discord import app_commands
from whitelist import has_himas_permission


def register(client: discord.Client):
    @client.tree.command(name="himas", description="Control Home Assistant with natural language")
    @app_commands.describe(command="Natural language command")
    async def himas(interaction: discord.Interaction, command: str):
        if not has_himas_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
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
