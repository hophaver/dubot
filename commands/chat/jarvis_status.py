import io
import json

import discord

from jarvis import jarvis_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(
        name="jarvis-status",
        description="DM only: show Jarvis tone, behaviour, and effective system additions",
    )
    async def jarvis_status(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ `/jarvis-status` works only in DMs.", ephemeral=True)
            return

        uid = interaction.user.id
        snap = jarvis_manager.get_status_snapshot(uid)
        full_addition = jarvis_manager.get_full_jarvis_system_addition(uid)
        if snap["enabled"]:
            file_header = (
                "This is the Jarvis-specific text appended after the base persona + chat system prompt "
                "for your DMs (learned profile block, then fixed behaviour).\n\n"
            )
        else:
            file_header = (
                "Jarvis is currently OFF — this is what would be appended after the base persona + chat "
                "system prompt if you enable `/jarvis`.\n\n"
            )
        file_body = file_header + full_addition
        attachment = discord.File(
            io.BytesIO(file_body.encode("utf-8")),
            filename="jarvis-system-addition.txt",
        )

        trusted = snap["trusted_commands"]
        trusted_str = ", ".join(trusted) if trusted else "(none)"
        if len(trusted_str) > 1020:
            trusted_str = trusted_str[:1010] + "…"

        pending = snap["pending_confirmation"]
        if pending is None:
            pending_str = "(none)"
        else:
            try:
                pending_str = json.dumps(pending, ensure_ascii=False, default=str)
            except TypeError:
                pending_str = str(pending)
            if len(pending_str) > 1020:
                pending_str = pending_str[:1010] + "…"

        last_ts = snap["last_tone_tuning_ts"]
        if last_ts > 0:
            last_tune = f"<t:{int(last_ts)}:R>"
        else:
            last_tune = "never"

        embed = discord.Embed(
            title="Jarvis status",
            description=(
                "The attached file is the **Jarvis-specific** system addition (learned preferences + fixed behaviour). "
                "The model also receives the global persona and the standard chat system prompt from `system_prompts.json`."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Jarvis enabled", value="yes" if snap["enabled"] else "no", inline=True)
        embed.add_field(name="Tone tuning runs", value=str(snap["tone_tuning_updates"]), inline=True)
        embed.add_field(name="Last tone tuning", value=last_tune, inline=True)
        embed.add_field(name="Messages queued for tuning", value=str(snap["tone_queue_len"]), inline=True)
        embed.add_field(name="Trusted no-confirm commands", value=trusted_str, inline=False)
        embed.add_field(name="Pending confirmation", value=pending_str, inline=False)

        await interaction.response.send_message(embed=embed, file=attachment)
