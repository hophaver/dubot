import io
import json

import discord

from adaptive_dm import adaptive_dm_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(
        name="adaptive-status",
        description="DMs: export adaptive context; reply with full adaptive-dm-context.txt to replace manual block",
    )
    async def adaptive_status(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ DMs only.", ephemeral=True)
            return

        uid = interaction.user.id
        label = (getattr(interaction.user, "global_name", None) or interaction.user.name or "").strip()
        adaptive_dm_manager.touch_adaptive_sync_display_name(uid, label)
        snap = adaptive_dm_manager.get_status_snapshot(uid)
        full_addition = adaptive_dm_manager.get_full_adaptive_system_addition(uid)
        if snap["enabled"]:
            file_header = "DM-specific addition (learned profile + fixed behaviour):\n\n"
        else:
            file_header = "Adaptive is **off** — this is what would be added when you turn it on:\n\n"
        file_body = file_header + full_addition
        attachment = discord.File(
            io.BytesIO(file_body.encode("utf-8")),
            filename="adaptive-dm-context.txt",
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
        last_tune = f"<t:{int(last_ts)}:R>" if last_ts > 0 else "never"

        if snap["enabled"]:
            desc_extra = (
                "Uses a minimal base + `chat_adaptive_dm` from `system_prompts.json` — **not** `/llm-settings` personas."
            )
        else:
            desc_extra = "Turn **adaptive** on to use this stack instead of your normal DM persona."

        embed = discord.Embed(
            title="Adaptive (DM)",
            description=(
                f"**Attachment:** full context block. {desc_extra}\n\n"
                "**Reply here** with the **entire** `adaptive-dm-context.txt` (paste all of it or attach the `.txt`): "
                "edit only the **manual** lines at the top; leave the **auto-learned** block and the **fixed behaviour** tail unchanged. "
                "That replaces the previous manual block; auto-tuning from your messages **continues**. "
                "**`reset manual`** clears only the manual block. "
                "On restart this syncs to **`personas.json`** as **`<your name> adaptive`**."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="On", value="yes" if snap["enabled"] else "no", inline=True)
        embed.add_field(
            name="Manual",
            value="yes" if snap.get("has_manual_override") else "no",
            inline=True,
        )
        embed.add_field(name="Tune runs", value=str(snap["tone_tuning_updates"]), inline=True)
        embed.add_field(name="Last tune", value=last_tune, inline=True)
        embed.add_field(name="Queued msgs", value=str(snap["tone_queue_len"]), inline=True)
        gc = snap.get("guild_tune_channel_id")
        ge = snap.get("guild_tune_channel_enabled")
        if gc and ge:
            gstr = f"on · <#{gc}>"
        elif gc and not ge:
            gstr = f"off · saved `{gc}` — use **`/adaptive-tune-channel`**"
        else:
            gstr = "off"
        embed.add_field(name="Channel tune", value=gstr[:1020], inline=False)
        embed.add_field(name="Trusted (no confirm)", value=trusted_str, inline=False)
        embed.add_field(name="Pending confirm", value=pending_str, inline=False)

        await interaction.response.send_message(embed=embed, file=attachment)
        try:
            status_msg = await interaction.original_response()
            adaptive_dm_manager.set_status_reply_anchor(uid, status_msg.channel.id, status_msg.id)
        except Exception:
            pass
