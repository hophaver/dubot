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
            from utils.ha_integration import ask_home_assistant
            from models import model_manager
            response = await ask_home_assistant(command, interaction.user.id)
            if not response or not response.strip():
                response = "No response. Check your command or use /listentities to see available devices."
            local_runtime = model_manager.get_last_local_model(interaction.user.id, refresh_local=False)
            embed = discord.Embed(title="🏠 Home Assistant", description=response[:4096], color=discord.Color.blue())
            embed.set_footer(text=f"LLM parsing model: local `{local_runtime}`")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
