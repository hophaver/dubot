from typing import Optional, Union

import discord
from discord import app_commands

from jarvis import jarvis_manager
from whitelist import get_user_permission

TextlikeChannel = Union[discord.TextChannel, discord.Thread]


def _textlike_channel(ch) -> Optional[TextlikeChannel]:
    if isinstance(ch, (discord.TextChannel, discord.Thread)):
        return ch
    return None


def register(client: discord.Client):
    @client.tree.command(
        name="adaptive-tune-channel",
        description="Tune your adaptive profile from a server channel (same profile as DMs; your messages only)",
    )
    @app_commands.describe(
        enabled="On: your messages in the saved channel also tune your profile. Off: only DMs tune (channel id kept unless you clear it).",
        channel="Text or thread channel (defaults to the channel you run this in, when in a server)",
        clear_stored_channel="When disabling: also forget which channel was saved",
    )
    async def adaptive_tune_channel(
        interaction: discord.Interaction,
        enabled: bool,
        channel: Optional[Union[discord.TextChannel, discord.Thread]] = None,
        clear_stored_channel: bool = False,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        uid = interaction.user.id
        resolved: Optional[TextlikeChannel] = None
        if channel is not None:
            resolved = _textlike_channel(channel)
        if resolved is None:
            resolved = _textlike_channel(interaction.channel)

        if enabled:
            if not jarvis_manager.is_enabled(uid):
                await interaction.response.send_message(
                    "Turn on adaptive assistant in DMs first (toggle with **`/jarvis`** enabled: on), then enable channel tuning.",
                    ephemeral=True,
                )
                return
            if resolved is None:
                await interaction.response.send_message(
                    "Pick a **text or thread** channel, or run this command **inside** the server channel you want to use.",
                    ephemeral=True,
                )
                return
            perms = resolved.permissions_for(interaction.user)
            if not (perms.read_messages and perms.view_channel):
                await interaction.response.send_message(
                    "❌ You need read access to that channel.", ephemeral=True
                )
                return
            jarvis_manager.set_guild_tune_channel(
                uid,
                enabled=True,
                channel_id=resolved.id,
                clear_channel_id=False,
            )
            label = f"#{resolved.name}" if hasattr(resolved, "name") else str(resolved.id)
            await interaction.response.send_message(
                f"✅ Channel tuning **on**. Your messages in **{label}** (`{resolved.id}`) update the **same** adaptive profile as your DMs. "
                f"URLs are ignored for tuning.\n"
                f"Turn off with **`/adaptive-tune-channel` enabled: false** (DM tuning continues).",
                ephemeral=True,
            )
            return

        jarvis_manager.set_guild_tune_channel(
            uid,
            enabled=False,
            channel_id=None,
            clear_channel_id=bool(clear_stored_channel),
        )
        if clear_stored_channel:
            await interaction.response.send_message(
                "✅ Channel tuning **off**, and the saved channel was cleared. DM tuning is unchanged.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "✅ Channel tuning **off**. Your saved channel is kept—turn **enabled** back on to resume without picking again. "
                "DM tuning continues.",
                ephemeral=True,
            )
