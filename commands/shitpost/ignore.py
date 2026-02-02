import discord
from discord import app_commands
from whitelist import is_admin
from . import _blacklist


def register(client):
    @client.tree.command(name="ignore", description="Add a word to shitpost ignore list (admin)")
    @app_commands.describe(word="Word to ignore (e.g. !word / .word will do nothing)")
    async def ignore(interaction: discord.Interaction, word: str):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        word_clean = (word or "").strip().lower()
        if not word_clean:
            await interaction.response.send_message("❌ Provide a word to ignore.", ephemeral=True)
            return
        added = _blacklist.add_ignored(word_clean)
        if added:
            await interaction.response.send_message(f"✅ `{word_clean}` added to shitpost ignore list.")
        else:
            await interaction.response.send_message(f"ℹ️ `{word_clean}` is already ignored.", ephemeral=True)
