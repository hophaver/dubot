from typing import Optional

import discord
from discord import app_commands

from integrations import PERMANENT_ADMIN
from services import remover_service


def register(client: discord.Client):
    @client.tree.command(
        name="remover",
        description="Set an emoji; global admin reacts with it to delete messages (permanent admin only)",
    )
    @app_commands.describe(emoji="Unicode or <:custom:123> (omit to pick via reaction on the next line)")
    async def remover_cmd(interaction: discord.Interaction, emoji: Optional[str] = None):
        if interaction.user.id != PERMANENT_ADMIN:
            await interaction.response.send_message("❌ Permanent admin only.", ephemeral=True)
            return

        if emoji is not None and str(emoji).strip():
            try:
                key = remover_service.parse_emoji_input(str(emoji))
            except Exception:
                await interaction.response.send_message("❌ Invalid emoji.", ephemeral=True)
                return
            remover_service.set_remover_emoji(key)
            await interaction.response.send_message(
                f"✅ Remover emoji set to {key}. React with it on a message to delete "
                "(servers: any message; DMs: bot messages only).",
                ephemeral=True,
            )
            return

        # Non-ephemeral so reactions work; brief text. Works for /remover and !remover (message proxy).
        await interaction.response.send_message("React with your remover emoji.", ephemeral=False)
        proxy_msg = getattr(interaction.response, "_original_message", None)
        if proxy_msg is not None:
            msg = proxy_msg
        else:
            msg = await interaction.original_response()
        remover_service.register_pending_setup(msg.channel.id, msg.id)
