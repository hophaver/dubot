import discord
from discord import app_commands
from whitelist import is_admin
from personas import persona_manager


def register(client: discord.Client):
    @client.tree.command(name="persona-create", description="[Admin] Create a new persona")
    @app_commands.describe(name="Name for the new persona", description="Persona description/prompt")
    async def persona_create(interaction: discord.Interaction, name: str, description: str):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        if persona_manager.persona_exists(name):
            await interaction.response.send_message(f"❌ Persona '{name}' already exists.")
            return
        persona_manager.create_persona(name, description)
        await interaction.response.send_message(f"✅ Persona '{name}' created successfully!")
