import discord
from discord import app_commands
from commands.shared import require_admin
from ._shared import load_ha_mappings, save_ha_mappings


def register(client: discord.Client):
    @client.tree.command(name="explain", description="Add a friendly name mapping for Home Assistant")
    @app_commands.describe(friendly_name="Friendly name (e.g., 'living room light')", entity_id="Entity ID (e.g., 'light.living_room')")
    async def explain(interaction: discord.Interaction, friendly_name: str, entity_id: str):
        if not await require_admin(interaction):
            return
        mappings = load_ha_mappings()
        mappings[friendly_name.lower()] = entity_id
        save_ha_mappings(mappings)
        await interaction.response.send_message(f"✅ Mapped `{friendly_name}` to `{entity_id}`")
