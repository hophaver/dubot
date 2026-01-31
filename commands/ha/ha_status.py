import json
import os
import discord
import requests
from whitelist import has_himas_permission
from integrations import HA_URL, HA_ACCESS_TOKEN
from ._shared import HA_MAPPINGS_FILE


def register(client: discord.Client):
    @client.tree.command(name="ha-status", description="Check Home Assistant connection and entities")
    async def ha_status(interaction: discord.Interaction):
        if not has_himas_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            headers = {"Authorization": f"Bearer {HA_ACCESS_TOKEN}", "Content-Type": "application/json"}
            response = requests.get(f"{HA_URL}/api/", headers=headers, timeout=10)
            embed = discord.Embed(title="🏠 Home Assistant Status", color=discord.Color.green() if response.status_code == 200 else discord.Color.red())
            if response.status_code == 200:
                data = response.json()
                embed.add_field(name="Status", value="✅ Connected", inline=True)
                embed.add_field(name="Version", value=data.get("message", "Unknown"), inline=True)
                entities_response = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=10)
                if entities_response.status_code == 200:
                    embed.add_field(name="Entities", value=str(len(entities_response.json())), inline=True)
                if os.path.exists(HA_MAPPINGS_FILE):
                    embed.add_field(name="Mappings", value=str(len(json.load(open(HA_MAPPINGS_FILE)))), inline=True)
            else:
                embed.add_field(name="Status", value="❌ Disconnected", inline=True)
                embed.add_field(name="Error", value=f"HTTP {response.status_code}", inline=True)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
