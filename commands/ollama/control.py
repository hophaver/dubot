"""Admin-only /ollama-on and /ollama-off to start/stop Ollama server."""
import discord
from discord import app_commands
from whitelist import is_admin
from utils.ollama import check_ollama_running, start_ollama, stop_ollama


def register(client: discord.Client):
    @client.tree.command(name="ollama-on", description="Start Ollama server (admin only)")
    async def ollama_on(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = start_ollama()
        await interaction.followup.send(f"✅ {msg}" if ok else f"❌ {msg}", ephemeral=True)

    @client.tree.command(name="ollama-off", description="Stop Ollama server (admin only)")
    async def ollama_off(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = stop_ollama()
        await interaction.followup.send(f"✅ {msg}" if ok else f"❌ {msg}", ephemeral=True)
