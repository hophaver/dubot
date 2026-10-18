from typing import Optional, Union

import discord
from discord import app_commands

from services.notes_service import build_notes_payload, notes_manager, run_update
from utils import dm_background
from whitelist import get_user_permission

TextlikeChannel = Union[discord.TextChannel, discord.Thread]


def register(client: discord.Client):
    @client.tree.command(
        name="notes",
        description="Project notes: no channel = show this channel's notes; with channel = toggle note-taking there",
    )
    @app_commands.describe(channel="Channel to turn note-taking on/off (omit to view notes for this channel)")
    async def notes(interaction: discord.Interaction, channel: Optional[TextlikeChannel] = None):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if channel is not None:
            await _toggle(client, interaction, channel)
            return

        here = interaction.channel
        if not isinstance(here, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "Run `/notes` in a server channel, or `/notes channel:#x` to turn it on.", ephemeral=True
            )
            return
        if not notes_manager.has_channel(here.id):
            await interaction.response.send_message(
                f"Note-taking is off here. Turn it on with `/notes channel:#{here.name}`.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        if notes_manager.is_enabled(here.id):
            try:
                await run_update(client, here.id)
            except Exception:
                pass
        embed, attachment = build_notes_payload(here.id, here.name)
        sent = await interaction.followup.send(embed=embed, file=attachment, wait=True)
        notes_manager.register_anchor(sent.id, here.id)


async def _toggle(client: discord.Client, interaction: discord.Interaction, channel: TextlikeChannel):
    user_perms = channel.permissions_for(interaction.user)
    if not (user_perms.view_channel and user_perms.read_message_history):
        await interaction.response.send_message("❌ You need read access to that channel.", ephemeral=True)
        return

    if notes_manager.is_enabled(channel.id):
        notes_manager.set_enabled(channel.id, False)
        await interaction.response.send_message(
            f"✅ Note-taking **off** in {channel.mention}. Notes are kept; turning it back on catches up on missed messages."
        )
        return

    me = channel.guild.me
    bot_perms = channel.permissions_for(me)
    if not (bot_perms.view_channel and bot_perms.read_message_history):
        await interaction.response.send_message(
            f"❌ I need **View Channel** and **Read Message History** in {channel.mention}.", ephemeral=True
        )
        return

    first_time = not notes_manager.get_state(channel.id).get("last_processed_message_id")
    notes_manager.set_enabled(channel.id, True, user_id=interaction.user.id, guild_id=channel.guild.id)
    scan = "Scanning the full channel history" if first_time else "Catching up on messages since it was last on"
    await interaction.response.send_message(
        f"✅ Note-taking **on** in {channel.mention}. {scan}… I'll post the notes here when done."
    )

    out_channel = interaction.channel

    async def _backfill():
        try:
            await run_update(client, channel.id)
        except Exception:
            pass
        if out_channel is None:
            return
        try:
            embed, attachment = build_notes_payload(channel.id, channel.name)
            sent = await out_channel.send(embed=embed, file=attachment)
            notes_manager.register_anchor(sent.id, channel.id)
        except Exception:
            pass

    dm_background.spawn(_backfill(), name=f"notes_backfill_{channel.id}")
