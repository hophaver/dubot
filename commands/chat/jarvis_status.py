import io
import json

import discord

from jarvis import jarvis_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(
        name="jarvis-status",
        description="DM only: show adaptive DM tone, behaviour, and effective system additions",
    )
    async def jarvis_status(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ This command works only in DMs.", ephemeral=True)
            return

        uid = interaction.user.id
        label = (getattr(interaction.user, "global_name", None) or interaction.user.name or "").strip()
        jarvis_manager.touch_adaptive_sync_display_name(uid, label)
        snap = jarvis_manager.get_status_snapshot(uid)
        full_addition = jarvis_manager.get_full_jarvis_system_addition(uid)
        if snap["enabled"]:
            file_header = (
                "This is the DM-specific text appended after the base persona + chat system prompt "
                "for your DMs (learned profile block, then fixed behaviour).\n\n"
            )
        else:
            file_header = (
                "Adaptive assistant is currently off — this is what would be appended after the base persona + chat "
                "system prompt when you turn it on.\n\n"
            )
        file_body = file_header + full_addition
        attachment = discord.File(
            io.BytesIO(file_body.encode("utf-8")),
            filename="dm-assistant-system-addition.txt",
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

        if snap["enabled"]:
            desc_extra = (
                "With adaptive assistant **on**, the model uses a **minimal base assistant** plus the `chat_adaptive_dm` "
                "template from `system_prompts.json` (commands, date, etc.)—**not** your non-adaptive `/llm-settings` personas."
            )
        else:
            desc_extra = (
                "When you turn adaptive assistant **on**, the model will use a minimal base plus `chat_adaptive_dm` "
                "from `system_prompts.json`, not your global persona."
            )
        embed = discord.Embed(
            title="Adaptive DM assistant",
            description=(
                "The attached file is the **DM-specific** addition (user context + fixed behaviour). "
                f"{desc_extra}\n\n"
                "**Reply to this message** with an edited version of that text (or only the user-context part) to "
                "set **manual** context (shown together with auto-learned notes). Auto tuning still updates in the background; "
                "send **`reset manual`** here to drop only the manual block. "
                "On each bot restart, this bundle is also written into **`personas.json`** as "
                "**`<your Discord display name> adaptive`** (e.g. `.dubyu adaptive`), using your name from your last DM or slash command."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Assistant enabled", value="yes" if snap["enabled"] else "no", inline=True)
        embed.add_field(
            name="Manual context",
            value="yes" if snap.get("has_manual_override") else "no",
            inline=True,
        )
        embed.add_field(name="Tone tuning runs", value=str(snap["tone_tuning_updates"]), inline=True)
        embed.add_field(name="Last tone tuning", value=last_tune, inline=True)
        embed.add_field(name="Messages queued for tuning", value=str(snap["tone_queue_len"]), inline=True)
        gc = snap.get("guild_tune_channel_id")
        ge = snap.get("guild_tune_channel_enabled")
        if gc and ge:
            gstr = f"on · `<#{gc}>` (`{gc}`)"
        elif gc and not ge:
            gstr = f"off · saved channel id `{gc}` (re-enable with **`/adaptive-tune-channel`**)"
        else:
            gstr = "off · no channel saved"
        embed.add_field(name="Server channel tuning", value=gstr[:1020], inline=False)
        embed.add_field(name="Trusted no-confirm commands", value=trusted_str, inline=False)
        embed.add_field(name="Pending confirmation", value=pending_str, inline=False)

        await interaction.response.send_message(embed=embed, file=attachment)
        try:
            status_msg = await interaction.original_response()
            jarvis_manager.set_status_reply_anchor(uid, status_msg.channel.id, status_msg.id)
        except Exception:
            pass
