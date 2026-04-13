"""Clone mode: bot mirror and optional server-wide nickname clone (permanent admin only)."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Optional

import discord
from services.profanity_service import contains_profanity

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(_ROOT, "assets")
STATE_PATH = os.path.join(_ROOT, "data", "clone_state.json")

# One member at a time; space out PATCH /guilds/.../members/... to avoid rate limits.
SERVER_WIDE_PER_MEMBER_DELAY_SEC = 2.5


def _extra_backoff_for_http_exception(exc: discord.HTTPException) -> float:
    if exc.status != 429:
        return 0.0
    try:
        ra = exc.response.headers.get("Retry-After")
        if ra is not None:
            return float(ra) + 1.0
    except (TypeError, ValueError, AttributeError):
        pass
    return 8.0

_default_state: Dict[str, Any] = {
    "active": False,
    "variant": None,
    "target_user_id": None,
    "delete_original": False,
    "guild_id": None,
    "original_nickname": None,
    "mirror_avatar_url": None,
    "server_wide": None,
}


def _ensure_dirs() -> None:
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)


def _avatar_basename(is_animated: bool) -> str:
    return "original_avatar.gif" if is_animated else "original_avatar.png"


def _avatar_path(is_animated: bool) -> str:
    return os.path.join(ASSETS_DIR, _avatar_basename(is_animated))


def _find_saved_avatar_path() -> Optional[str]:
    for name in ("original_avatar.png", "original_avatar.gif", "original_avatar.webp"):
        p = os.path.join(ASSETS_DIR, name)
        if os.path.isfile(p):
            return p
    return None


def load_state() -> Dict[str, Any]:
    _ensure_dirs()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_default_state)
    out = dict(_default_state)
    out.update(data)
    return out


def save_state(state: Dict[str, Any]) -> None:
    _ensure_dirs()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _effective_variant(state: Dict[str, Any]) -> Optional[str]:
    if not state.get("active"):
        return None
    v = state.get("variant")
    if v:
        return v
    return "bot_mirror"


def is_clone_active() -> bool:
    return bool(load_state().get("active"))


def get_clone_target_user_id() -> Optional[int]:
    s = load_state()
    if not s.get("active") or _effective_variant(s) != "bot_mirror":
        return None
    tid = s.get("target_user_id")
    return int(tid) if tid is not None else None


def get_clone_guild_id() -> Optional[int]:
    s = load_state()
    gid = s.get("guild_id")
    return int(gid) if gid is not None else None


def should_delete_original() -> bool:
    return bool(load_state().get("delete_original"))


def _nick_norm(n: Optional[str]) -> str:
    return (n or "").strip()


def _avatar_key(member: discord.Member) -> str:
    return str(member.display_avatar.key)


async def snapshot_baseline_avatar(client: discord.Client) -> None:
    """While not cloning, persist current bot avatar to assets (used as restore baseline)."""
    if not client.user:
        return
    if load_state().get("active"):
        return
    _ensure_dirs()
    u = client.user
    animated = u.display_avatar.is_animated()
    path = _avatar_path(animated)
    other = _avatar_path(not animated)
    if os.path.isfile(other):
        try:
            os.remove(other)
        except OSError:
            pass
    data = await u.display_avatar.read()
    with open(path, "wb") as f:
        f.write(data)


async def _apply_avatar_bytes(client: discord.Client, data: bytes) -> None:
    if not client.user:
        return
    await client.user.edit(avatar=data)


async def _apply_avatar_from_member(client: discord.Client, member: discord.Member) -> None:
    data = await member.display_avatar.read()
    await _apply_avatar_bytes(client, data)


async def restore_original_appearance(client: discord.Client, guild: Optional[discord.Guild]) -> None:
    path = _find_saved_avatar_path()
    if path and client.user:
        with open(path, "rb") as f:
            await _apply_avatar_bytes(client, f.read())
    if guild and guild.me:
        state = load_state()
        nick = state.get("original_nickname")
        await guild.me.edit(nick=nick)


async def _revert_server_wide(client: discord.Client, guild: Optional[discord.Guild]) -> None:
    state = load_state()
    sw = state.get("server_wide")
    if not sw or not isinstance(sw, dict):
        return
    if not guild:
        return
    members_snap = sw.get("members") or {}
    delay = SERVER_WIDE_PER_MEMBER_DELAY_SEC
    for uid_str, snap in members_snap.items():
        await asyncio.sleep(delay)
        try:
            uid = int(uid_str)
        except (TypeError, ValueError):
            continue
        member = guild.get_member(uid)
        if not member or member.bot:
            continue
        cur_nick = member.nick
        cur_key = _avatar_key(member)
        post_nick = snap.get("post_nick")
        post_key = snap.get("post_avatar_key")
        if _nick_norm(cur_nick) != _nick_norm(post_nick) or cur_key != post_key:
            continue
        orig_nick = snap.get("orig_nick")
        try:
            await member.edit(nick=orig_nick)
        except discord.HTTPException as e:
            await asyncio.sleep(_extra_backoff_for_http_exception(e))


async def revert_if_active(client: discord.Client) -> None:
    state = load_state()
    if not state.get("active"):
        return
    variant = _effective_variant(state)
    gid = state.get("guild_id")
    guild = client.get_guild(int(gid)) if gid is not None else None
    if variant == "server_wide":
        await _revert_server_wide(client, guild)
    else:
        await restore_original_appearance(client, guild)
    save_state(dict(_default_state))
    await snapshot_baseline_avatar(client)


async def recover_stale_clone_on_startup(client: discord.Client) -> None:
    """If clone was left active (crash), revert and clear state."""
    state = load_state()
    if state.get("active"):
        await revert_if_active(client)


async def on_bot_ready_baseline(client: discord.Client) -> None:
    await recover_stale_clone_on_startup(client)
    await snapshot_baseline_avatar(client)


async def start_clone(
    client: discord.Client,
    member: discord.Member,
    delete_original: bool,
) -> None:
    guild = member.guild
    if not guild.me:
        raise RuntimeError("Bot member not available")
    if load_state().get("active"):
        await revert_if_active(client)
    await snapshot_baseline_avatar(client)
    path = _find_saved_avatar_path()
    if not path and client.user:
        u = client.user
        animated = u.display_avatar.is_animated()
        p = _avatar_path(animated)
        data = await u.display_avatar.read()
        with open(p, "wb") as f:
            f.write(data)
    stored_original_nick = guild.me.nick
    state = {
        "active": True,
        "variant": "bot_mirror",
        "target_user_id": member.id,
        "delete_original": delete_original,
        "guild_id": guild.id,
        "original_nickname": stored_original_nick,
        "mirror_avatar_url": str(member.display_avatar.url),
        "server_wide": None,
    }
    save_state(state)
    await _apply_avatar_from_member(client, member)
    await guild.me.edit(nick=member.display_name)


async def start_server_wide_clone(client: discord.Client, template: discord.Member) -> tuple[int, int]:
    """
    Snapshot all non-bot members, set nick to template display name where the API allows.
    Returns (edited_ok, edit_failed).
    Discord does not allow bots to change other users' avatars; avatar URL/key per member is stored for restore rules.
    """
    guild = template.guild
    if not guild.me:
        raise RuntimeError("Bot member not available")
    if load_state().get("active"):
        await revert_if_active(client)

    try:
        await guild.chunk()
    except Exception:
        pass

    if not guild.me.guild_permissions.manage_nicknames and not guild.me.guild_permissions.administrator:
        raise RuntimeError("Bot needs Manage Nicknames (or Administrator) to run server-wide clone.")

    template_nick = template.display_name[:32]
    members_map: Dict[str, Dict[str, Any]] = {}

    for member in list(guild.members):
        if member.bot:
            continue
        orig_nick = member.nick
        orig_key = _avatar_key(member)
        members_map[str(member.id)] = {
            "orig_nick": orig_nick,
            "orig_avatar_key": orig_key,
            "orig_avatar_url": str(member.display_avatar.url),
            "post_nick": orig_nick,
            "post_avatar_key": orig_key,
        }

    state = {
        "active": True,
        "variant": "server_wide",
        "target_user_id": None,
        "delete_original": False,
        "guild_id": guild.id,
        "original_nickname": None,
        "mirror_avatar_url": None,
        "server_wide": {
            "template_user_id": template.id,
            "members": members_map,
        },
    }
    save_state(state)

    ok = 0
    failed = 0
    delay = SERVER_WIDE_PER_MEMBER_DELAY_SEC

    for member in list(guild.members):
        if member.bot:
            continue
        await asyncio.sleep(delay)
        uid = str(member.id)
        snap = members_map.get(uid)
        if not snap:
            continue
        if _nick_norm(member.nick) == _nick_norm(template_nick):
            snap["post_nick"] = member.nick
            snap["post_avatar_key"] = _avatar_key(member)
            continue
        try:
            await member.edit(nick=template_nick)
            snap["post_nick"] = template_nick
            snap["post_avatar_key"] = _avatar_key(member)
            ok += 1
        except discord.HTTPException as e:
            failed += 1
            m2 = guild.get_member(member.id)
            if m2:
                snap["post_nick"] = m2.nick
                snap["post_avatar_key"] = _avatar_key(m2)
            await asyncio.sleep(_extra_backoff_for_http_exception(e))

    state["server_wide"]["members"] = members_map
    save_state(state)
    return ok, failed


async def stop_clone(client: discord.Client) -> None:
    await revert_if_active(client)


async def refresh_clone_appearance(client: discord.Client, guild: Optional[discord.Guild]) -> None:
    state = load_state()
    if not state.get("active") or _effective_variant(state) != "bot_mirror":
        return
    gid = state.get("guild_id")
    g = guild or (client.get_guild(int(gid)) if gid is not None else None)
    if not g:
        return
    member = g.get_member(int(state["target_user_id"]))
    if not member:
        return
    url = str(member.display_avatar.url)
    if state.get("mirror_avatar_url") != url:
        await _apply_avatar_from_member(client, member)
        state["mirror_avatar_url"] = url
        save_state(state)
    if g.me and g.me.nick != member.display_name:
        await g.me.edit(nick=member.display_name)


async def sync_identity(client: discord.Client, guild: Optional[discord.Guild]) -> None:
    """Refresh baseline on disk when inactive; refresh bot mirror when active (/status, /help)."""
    state = load_state()
    if state.get("active") and _effective_variant(state) == "bot_mirror":
        await refresh_clone_appearance(client, guild)
    else:
        await snapshot_baseline_avatar(client)


async def mirror_message_if_clone(client: discord.Client, message: discord.Message) -> bool:
    """
    If bot-mirror clone is active and this message is from the target, echo it and optionally delete the original.
    Returns True if handled (caller should skip further processing).
    """
    if not message.guild:
        return False
    state = load_state()
    if not state.get("active"):
        return False
    if _effective_variant(state) != "bot_mirror":
        return False
    if int(state["guild_id"]) != message.guild.id:
        return False
    if int(state["target_user_id"]) != message.author.id:
        return False
    if message.author.bot:
        return False

    content = message.content
    files = []
    for a in message.attachments:
        try:
            files.append(await a.to_file())
        except Exception:
            pass

    if not (content or "").strip() and not files:
        return True

    clean_content = (content or "").strip()
    kwargs: Dict[str, Any] = {}
    if clean_content and contains_profanity(clean_content):
        sanitized = clean_content.replace('"', '\\"')
        kwargs["content"] = f'{message.author.mention} TRIED TO ABUSE BOT AND SAY "{sanitized}"'
    else:
        if clean_content:
            kwargs["content"] = content
        if files:
            kwargs["files"] = files
    try:
        await message.channel.send(**kwargs)
    except discord.HTTPException:
        return True

    if state.get("delete_original"):
        me = message.guild.me
        if me and me.guild_permissions.manage_messages:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
    return True
