from commands.shared import require_himas
import discord
from utils.ha_integration import ha_manager
from ._shared import load_ha_mappings


def register(client: discord.Client):
    @client.tree.command(name="ha-status", description="Check Home Assistant connection and entities")
    async def ha_status(interaction: discord.Interaction):
        if not await require_himas(interaction):
            return
        await interaction.response.defer()
        status, data = await ha_manager.get_api_status()
        ok = status == 200
        embed = discord.Embed(
            title="🏠 Home Assistant Status", color=discord.Color.green() if ok else discord.Color.red()
        )
        if ok:
            embed.add_field(name="Status", value="✅ Connected", inline=True)
            embed.add_field(name="Version", value=data.get("message", "Unknown"), inline=True)
            entities = await ha_manager.get_all_entities(force_refresh=True)
            embed.add_field(name="Entities", value=str(len(entities)), inline=True)
            embed.add_field(name="Mappings", value=str(len(load_ha_mappings())), inline=True)
        else:
            embed.add_field(name="Status", value="❌ Disconnected", inline=True)
            embed.add_field(name="Error", value=f"HTTP {status}" if status else "Request failed", inline=True)
        await interaction.followup.send(embed=embed)
