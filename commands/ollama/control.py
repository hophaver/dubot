"""Admin-only /ollama-on and /ollama-off to start/stop Ollama server."""
import discord
from commands.shared import require_admin
from models import model_manager
from utils.ollama import start_ollama_async, stop_ollama_async


def register(client: discord.Client):
    @client.tree.command(name="ollama-on", description="Start Ollama server (admin only)")
    async def ollama_on(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = await start_ollama_async()
        await interaction.followup.send(f"✅ {msg}" if ok else f"❌ {msg}", ephemeral=True)
        await model_manager.refresh_async()

    @client.tree.command(name="ollama-off", description="Stop Ollama server (admin only)")
    async def ollama_off(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = await stop_ollama_async()
        await interaction.followup.send(f"✅ {msg}" if ok else f"❌ {msg}", ephemeral=True)
        await model_manager.refresh_async()
