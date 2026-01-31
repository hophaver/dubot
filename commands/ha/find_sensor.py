from typing import Optional
import discord
from discord import app_commands
import requests
from whitelist import has_himas_permission
from integrations import HA_URL, HA_ACCESS_TOKEN


def register(client: discord.Client):
    @client.tree.command(name="find-sensor", description="Find and query sensors")
    @app_commands.describe(search="Search term for sensors")
    async def find_sensor(interaction: discord.Interaction, search: Optional[str] = None):
        if not has_himas_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            headers = {"Authorization": f"Bearer {HA_ACCESS_TOKEN}", "Content-Type": "application/json"}
            response = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=10)
            if response.status_code != 200:
                await interaction.followup.send(f"❌ Failed to get entities: HTTP {response.status_code}")
                return
            entities = response.json()
            if search:
                entities = [e for e in entities if search.lower() in e["entity_id"].lower()]
            if not entities:
                await interaction.followup.send(f"🔍 No entities found{(' matching \"' + search + '\"') if search else ''}.")
                return
            embed = discord.Embed(title=f"🔍 Sensors{' matching \"' + search + '\"' if search else ''}", color=discord.Color.blue())
            for entity in entities[:10]:
                state = entity.get("state", "unknown")
                friendly_name = entity.get("attributes", {}).get("friendly_name", entity["entity_id"])
                embed.add_field(name=friendly_name, value=f"**ID:** `{entity['entity_id']}`\n**State:** {state}", inline=False)
            if len(entities) > 10:
                embed.set_footer(text=f"And {len(entities) - 10} more entities...")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
