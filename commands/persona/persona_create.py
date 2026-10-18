import discord
from discord import app_commands
from personas import persona_manager
from commands.shared import require_admin


def register(client: discord.Client):
    @client.tree.command(name="persona-create", description="[Admin] Create a new persona")
    @app_commands.describe(name="Name for the new persona", description="Persona description/prompt")
    async def persona_create(interaction: discord.Interaction, name: str, description: str):
        if not await require_admin(interaction):
            return
        if persona_manager.persona_exists(name):
            await interaction.response.send_message(f"❌ Persona '{name}' already exists.")
            return
        persona_manager.create_persona(name, description)
        await interaction.response.send_message(f"✅ Persona '{name}' created successfully!")
