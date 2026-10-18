import discord
from discord import app_commands
from commands.shared import require_admin
from ._shared import load_ha_mappings, save_ha_mappings


def register(client: discord.Client):
    @client.tree.command(name="removeentity", description="Remove an entity mapping")
    @app_commands.describe(friendly_name="Friendly name to remove")
    async def removeentity(interaction: discord.Interaction, friendly_name: str):
        if not await require_admin(interaction):
            return
        mappings = load_ha_mappings()
        key = friendly_name.lower()
        if key not in mappings:
            await interaction.response.send_message(f"❌ No mapping found for `{friendly_name}`")
            return
        del mappings[key]
        save_ha_mappings(mappings)
        await interaction.response.send_message(f"✅ Removed mapping for `{friendly_name}`")
