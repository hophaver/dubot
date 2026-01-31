import json
import os
import discord
from discord import app_commands
from whitelist import has_himas_permission
from ._shared import HA_MAPPINGS_FILE


def register(client: discord.Client):
    @client.tree.command(name="explain", description="Add a friendly name mapping for Home Assistant")
    @app_commands.describe(friendly_name="Friendly name (e.g., 'living room light')", entity_id="Entity ID (e.g., 'light.living_room')")
    async def explain(interaction: discord.Interaction, friendly_name: str, entity_id: str):
        if not has_himas_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        try:
            mappings = json.load(open(HA_MAPPINGS_FILE)) if os.path.exists(HA_MAPPINGS_FILE) else {}
            mappings[friendly_name.lower()] = entity_id
            os.makedirs(os.path.dirname(HA_MAPPINGS_FILE), exist_ok=True)
            with open(HA_MAPPINGS_FILE, "w") as f:
                json.dump(mappings, f, indent=2)
            await interaction.response.send_message(f"✅ Mapped `{friendly_name}` to `{entity_id}`")
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)[:100]}")
