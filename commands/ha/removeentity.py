import json
import os
import discord
from discord import app_commands
from whitelist import is_admin
from ._shared import HA_MAPPINGS_FILE


def register(client: discord.Client):
    @client.tree.command(name="removeentity", description="Remove an entity mapping")
    @app_commands.describe(friendly_name="Friendly name to remove")
    async def removeentity(interaction: discord.Interaction, friendly_name: str):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        try:
            if not os.path.exists(HA_MAPPINGS_FILE):
                await interaction.response.send_message("❌ No entity mappings found.")
                return
            mappings = json.load(open(HA_MAPPINGS_FILE))
            key = friendly_name.lower()
            if key in mappings:
                del mappings[key]
                with open(HA_MAPPINGS_FILE, "w") as f:
                    json.dump(mappings, f, indent=2)
                await interaction.response.send_message(f"✅ Removed mapping for `{friendly_name}`")
            else:
                await interaction.response.send_message(f"❌ No mapping found for `{friendly_name}`")
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)[:100]}")
