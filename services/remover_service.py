"""Global-admin 'remover' emoji: react on a message to delete it (guild: any; DM: bot messages only)."""
from __future__ import annotations

from typing import Optional, Set, Tuple

import discord

from integrations import PERMANENT_ADMIN
from utils import home_log
from utils.json_store import load_json, save_json
from utils.paths import data_path

DATA_PATH = data_path("remover.json")

# (channel_id, message_id) awaiting first reaction to choose the remover emoji
_pending_setup: Set[Tuple[int, int]] = set()


def _load() -> dict:
    data = load_json(DATA_PATH, {})
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    save_json(DATA_PATH, data)


def get_remover_emoji() -> Optional[str]:
    raw = _load().get("emoji")
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    return s or None


def set_remover_emoji(emoji_key: str) -> None:
    data = _load()
    data["emoji"] = emoji_key.strip()
    _save(data)


def register_pending_setup(channel_id: int, message_id: int) -> None:
    _pending_setup.add((int(channel_id), int(message_id)))


def clear_pending_setup(channel_id: int, message_id: int) -> None:
    _pending_setup.discard((int(channel_id), int(message_id)))


def is_pending_setup(channel_id: int, message_id: int) -> bool:
    return (int(channel_id), int(message_id)) in _pending_setup


def parse_emoji_input(text: str) -> str:
    t = (text or "").strip()
    if not t:
        raise ValueError("empty emoji")
    pe = discord.PartialEmoji.from_str(t)
    return str(pe)


def reaction_key(emoji: discord.PartialEmoji) -> str:
    return str(emoji)


async def handle_raw_reaction_add(client: discord.Client, payload: discord.RawReactionActionEvent) -> None:
    if client.user is None:
        return
    if payload.user_id != PERMANENT_ADMIN:
        return

    channel = client.get_channel(payload.channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(payload.channel_id)
        except Exception:
            return

    emoji_key = reaction_key(payload.emoji)
    cid, mid = int(payload.channel_id), int(payload.message_id)

    if is_pending_setup(cid, mid):
        try:
            msg = await channel.fetch_message(mid)
        except Exception:
            clear_pending_setup(cid, mid)
            return
        set_remover_emoji(emoji_key)
        clear_pending_setup(cid, mid)
        try:
            await msg.edit(content=f"✅ Remover emoji set to {emoji_key}.")
        except Exception:
            pass
        return

    configured = get_remover_emoji()
    if not configured or emoji_key != configured:
        return

    try:
        target = await channel.fetch_message(mid)
    except (discord.NotFound, discord.Forbidden):
        return
    except Exception:
        return

    if isinstance(channel, discord.DMChannel):
        if target.author.id != client.user.id:
            return

    try:
        await target.delete()
    except discord.Forbidden:
        await home_log.send_to_home(
            f"⚠️ `/remover` delete failed (missing permissions) in channel `{cid}`."
        )
    except discord.NotFound:
        pass
    except Exception as exc:
        await home_log.send_to_home(f"⚠️ `/remover` delete error: {str(exc)[:200]}")
