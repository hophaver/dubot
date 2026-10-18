from commands.shared import require_himas
from typing import Optional
import discord
from discord import app_commands
from utils.ha_integration import ha_manager


def register(client: discord.Client):
    @client.tree.command(name="find-sensor", description="Find and query sensors")
    @app_commands.describe(search="Search term for sensors")
    async def find_sensor(interaction: discord.Interaction, search: Optional[str] = None):
        if not await require_himas(interaction):
            return
        await interaction.response.defer()
        entities = list((await ha_manager.get_all_entities()).values())
        if not entities:
            await interaction.followup.send("❌ Failed to get entities from Home Assistant.")
            return
        if search:
            entities = [e for e in entities if search.lower() in e["entity_id"].lower()]
        suffix = f' matching "{search}"' if search else ""
        if not entities:
            await interaction.followup.send(f"🔍 No entities found{suffix}.")
            return
        embed = discord.Embed(title=f"🔍 Sensors{suffix}", color=discord.Color.blue())
        for entity in entities[:10]:
            state = entity.get("state", "unknown")
            friendly_name = entity.get("attributes", {}).get("friendly_name", entity["entity_id"])
            embed.add_field(name=friendly_name, value=f"**ID:** `{entity['entity_id']}`\n**State:** {state}", inline=False)
        if len(entities) > 10:
            embed.set_footer(text=f"And {len(entities) - 10} more entities...")
        await interaction.followup.send(embed=embed)
