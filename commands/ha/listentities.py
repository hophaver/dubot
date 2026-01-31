import json
import os
import discord
from whitelist import has_himas_permission
from ._shared import HA_MAPPINGS_FILE


def register(client: discord.Client):
    @client.tree.command(name="listentities", description="List all entity mappings")
    async def listentities(interaction: discord.Interaction):
        if not has_himas_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        try:
            if not os.path.exists(HA_MAPPINGS_FILE):
                await interaction.response.send_message("📭 No entity mappings found.")
                return
            mappings = json.load(open(HA_MAPPINGS_FILE))
            if not mappings:
                await interaction.response.send_message("📭 No entity mappings found.")
                return
            embed = discord.Embed(title="🏠 Entity Mappings", color=discord.Color.green())
            for friendly_name, entity_id in mappings.items():
                embed.add_field(name=friendly_name, value=entity_id, inline=True)
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)[:100]}")
