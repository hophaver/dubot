"""Batch adaptive tuning: corpus from a full Discord message or .txt (slash string options cannot use newlines)."""

from __future__ import annotations

import re
from typing import Optional, Tuple

import discord
from discord import app_commands

from adaptive_dm import adaptive_dm_manager
from whitelist import get_user_permission

_DISCORD_MSG_URL = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(@me|\d+)/(\d+)/(\d+)",
    re.IGNORECASE,
)
_MAX_CHARS = 120_000


def _parse_message_url(url: str) -> Optional[Tuple[Optional[int], int, int]]:
    """Return (guild_id or None if @me, channel_id, message_id)."""
    m = _DISCORD_MSG_URL.search((url or "").strip())
    if not m:
        return None
    g_raw, ch_s, msg_s = m.group(1), m.group(2), m.group(3)
    guild_id: Optional[int] = None if g_raw == "@me" else int(g_raw)
    return guild_id, int(ch_s), int(msg_s)


async def _body_from_discord_link(client: discord.Client, user_id: int, url: str) -> str:
    parsed = _parse_message_url(url)
    if not parsed:
        raise ValueError(
            "Paste a **Discord message link** (right-click message → Copy Message Link), "
            "or attach a **`.txt`** file. Slash-command text fields cannot include newlines."
        )
    _guild_hint, channel_id, message_id = parsed
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception as exc:
            raise ValueError(
                f"I cannot access that channel ({channel_id}). Open the DM or server channel with the bot first. ({exc!s:.120})"
            ) from exc
    try:
        msg = await channel.fetch_message(message_id)
    except Exception as exc:
        raise ValueError(f"Could not load that message. ({exc!s:.120})") from exc
    if msg.author.id != user_id:
        raise ValueError("That message must be **your own** (same account as this command).")
    body = (msg.content or "").strip()
    if not body:
        raise ValueError(
            "That message has no text. Put your corpus in the message body, or attach a `.txt` file."
        )
    return body


def _resolve_file_attachment(interaction: discord.Interaction, explicit: Optional[discord.Attachment]) -> Optional[discord.Attachment]:
    """Discord sometimes omits injecting Attachment into the callback; read it from the interaction payload."""
    if explicit is not None:
        return explicit
    ns = getattr(interaction, "namespace", None)
    if ns is not None:
        try:
            cand = getattr(ns, "file", None)
            if isinstance(cand, discord.Attachment):
                return cand
        except Exception:
            pass
    data = getattr(interaction, "data", None)
    if not isinstance(data, dict):
        return None
    resolved = data.get("resolved") or {}
    raw_atts = resolved.get("attachments") or {}
    if not raw_atts:
        return None
    state = getattr(interaction, "_state", None)
    if state is None:
        return None
    for opt in data.get("options") or []:
        if opt.get("type") != 11:  # ApplicationCommandOptionType.attachment
            continue
        aid = opt.get("value")
        if aid is None:
            continue
        key = str(aid)
        payload = raw_atts.get(key)
        if payload is None and isinstance(aid, str) and aid.isdigit():
            payload = raw_atts.get(int(aid))
        if not isinstance(payload, dict):
            continue
        try:
            return discord.Attachment(data=payload, state=state)
        except Exception:
            continue
    if len(raw_atts) == 1:
        payload = next(iter(raw_atts.values()))
        if isinstance(payload, dict):
            try:
                return discord.Attachment(data=payload, state=state)
            except Exception:
                pass
    return None


def _is_text_file(att: discord.Attachment) -> bool:
    name = (att.filename or "").lower()
    ct = (att.content_type or "").lower()
    if name.endswith(".txt"):
        return True
    if ct.startswith("text/") or ct in ("text/plain", "text/txt"):
        return True
    if ct == "application/octet-stream" and (not name or name.endswith(".txt")):
        return True
    return False


async def _body_from_txt(attachment: discord.Attachment) -> str:
    if not _is_text_file(attachment):
        raise ValueError(
            "Upload a **`.txt`** or **plain text** file (or use a Discord message link). "
            "If you attached a file, use the **file** option on the command form, not only a message attachment."
        )
    data = await attachment.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def register(client: discord.Client):
    @client.tree.command(
        name="adaptive-tune-batch",
        description="DMs: tune adaptive from a full message or .txt (preserves newlines)",
    )
    @app_commands.describe(
        source_message_link="Optional: Copy Message Link — that message’s full text is the corpus",
        file="Attach here (required for uploads): .txt or plain text — use this field, not a loose message attachment",
    )
    async def adaptive_tune_batch(
        interaction: discord.Interaction,
        source_message_link: Optional[str] = None,
        file: Optional[discord.Attachment] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ DMs only.", ephemeral=True)
            return

        label = (getattr(interaction.user, "global_name", None) or interaction.user.name or "").strip()
        adaptive_dm_manager.touch_adaptive_sync_display_name(interaction.user.id, label)
        if not adaptive_dm_manager.is_enabled(interaction.user.id):
            await interaction.response.send_message(
                "Turn **adaptive** on first (`/adaptive` → on).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            resolved_file = _resolve_file_attachment(interaction, file)
            if resolved_file is not None:
                raw = await _body_from_txt(resolved_file)
            elif source_message_link and str(source_message_link).strip():
                raw = await _body_from_discord_link(
                    client, interaction.user.id, str(source_message_link).strip()
                )
            else:
                raise ValueError(
                    "Use the **`file`** option on this command to attach **`.txt`** (not only a message attachment), "
                    "or paste a **message link**."
                )
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ {str(e)[:200]}", ephemeral=True)
            return

        raw = raw.strip()
        if len(raw) > _MAX_CHARS:
            await interaction.followup.send(
                f"❌ Text is too long ({len(raw):,} chars). Max **{_MAX_CHARS:,}** characters.",
                ephemeral=True,
            )
            return

        ok, code = adaptive_dm_manager.apply_batch_tuning_text(interaction.user.id, raw)
        if not ok:
            if code == "adaptive_off":
                msg = "Adaptive is off."
            elif code == "empty":
                msg = "No usable text after cleaning (too short or only URLs)."
            else:
                msg = "Could not apply tuning."
            await interaction.followup.send(msg, ephemeral=True)
            return

        preview = raw.replace("\n", " ")
        if len(preview) > 160:
            preview = preview[:157] + "…"
        await interaction.followup.send(
            f"✅ Applied **{len(raw):,}** characters to your adaptive profile.\n"
            f"_Preview:_ {preview}",
            ephemeral=True,
        )
