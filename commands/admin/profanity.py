from typing import Optional

import discord
from discord import app_commands

from integrations import PERMANENT_ADMIN
from services import profanity_service


def _format_words(words: list[str], limit: int = 50) -> str:
    if not words:
        return "*No words configured.*"
    shown = ", ".join(f"`{w}`" for w in words[:limit])
    if len(words) > limit:
        shown += f"\n*...and {len(words) - limit} more*"
    return shown


def register(client: discord.Client):
    @client.tree.command(name="profanity", description="View/edit profanity list (permanent admin only)")
    @app_commands.describe(
        action="view, add, remove, or reset",
        word="Word for add/remove",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="view", value="view"),
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="reset", value="reset"),
        ]
    )
    async def profanity_cmd(
        interaction: discord.Interaction,
        action: str = "view",
        word: Optional[str] = None,
    ):
        if interaction.user.id != PERMANENT_ADMIN:
            await interaction.response.send_message("❌ Permanent admin only.", ephemeral=True)
            return

        action = (action or "view").strip().lower()
        if action in ("add", "remove") and not word:
            await interaction.response.send_message("❌ Provide `word` for add/remove.", ephemeral=True)
            return
        if action in ("view", "reset") and word:
            await interaction.response.send_message("❌ `word` is only used with add/remove.", ephemeral=True)
            return

        if action == "add":
            changed = profanity_service.add_word(word or "")
            words = profanity_service.get_words()
            if changed:
                await interaction.response.send_message(
                    f"✅ Added word. List now has **{len(words)}** entries.\n{_format_words(words)}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message("⚠️ Word already exists or invalid.", ephemeral=True)
            return

        if action == "remove":
            changed = profanity_service.remove_word(word or "")
            words = profanity_service.get_words()
            if changed:
                await interaction.response.send_message(
                    f"✅ Removed word. List now has **{len(words)}** entries.\n{_format_words(words)}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message("⚠️ Word not found (or invalid).", ephemeral=True)
            return

        if action == "reset":
            profanity_service.reset_defaults()
            words = profanity_service.get_words()
            await interaction.response.send_message(
                f"✅ Profanity list reset to defaults (**{len(words)}** words).\n{_format_words(words)}",
                ephemeral=True,
            )
            return

        words = profanity_service.get_words()
        await interaction.response.send_message(
            f"📋 Profanity list (**{len(words)}** words):\n{_format_words(words)}",
            ephemeral=True,
        )
