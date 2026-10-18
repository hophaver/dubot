"""Channel note taker: extracts product/project ideas from server channels into structured notes.

Runs in batches (message threshold, idle sweep, or on /notes), never per message.
Catch-up always reads Discord history after the last processed message id, so backfill,
periodic runs and restart recovery share one path.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from config import is_bot_awake
from llm_function_prefs import get_function_persona_name
from models import model_manager
from personas import persona_manager
from utils import dm_background

NOTES_STATE_FILE = "data/notes_state.json"
NOTES_EXPORT_FILENAME = "channel-notes.txt"

PENDING_THRESHOLD = 50
IDLE_SECONDS = 3600
LOOP_INTERVAL_SECONDS = 600
ARCHIVE_AFTER_SECONDS = 30 * 86400
CHUNK_CHARS = 10_000
MESSAGE_CAP_CHARS = 600
MAX_ANCHORS = 20

LIST_CAPS = {"key_points": 10, "decisions": 8, "open_questions": 6}
STATUSES = ("idea", "planning", "in progress", "paused", "done", "dropped")

_URL_RE = re.compile(r"https?://([^/\s>]+)[^\s>]*", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:48] or "project"


def _clean_str(v: Any, cap: int = 300) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:cap]


def _clean_list(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    return [s for s in (_clean_str(x, 240) for x in v) if s]


def _clean_status(v: Any) -> str:
    s = _clean_str(v, 20).lower()
    return s if s in STATUSES else ""


class NotesManager:
    def __init__(self, save_file: str = NOTES_STATE_FILE):
        self.save_file = save_file
        self.channels: Dict[str, Dict[str, Any]] = {}
        self.anchors: Dict[str, str] = {}  # notes view message id -> notes channel id
        self._load()

    def _get(self, channel_id: int) -> Dict[str, Any]:
        key = str(channel_id)
        st = self.channels.get(key)
        if st is None:
            st = {
                "enabled": False,
                "enabled_by": 0,
                "guild_id": 0,
                "last_processed_message_id": 0,
                "last_run_ts": 0.0,
                "projects": [],
                "undo_snapshot": None,
            }
            self.channels[key] = st
        return st

    def has_channel(self, channel_id: int) -> bool:
        return str(channel_id) in self.channels

    def is_enabled(self, channel_id: int) -> bool:
        st = self.channels.get(str(channel_id))
        return bool(st and st.get("enabled"))

    def set_enabled(self, channel_id: int, enabled: bool, user_id: int = 0, guild_id: int = 0) -> None:
        st = self._get(channel_id)
        st["enabled"] = bool(enabled)
        if enabled:
            st["enabled_by"] = int(user_id or st.get("enabled_by") or 0)
            st["guild_id"] = int(guild_id or st.get("guild_id") or 0)
        self.save()

    def enabled_channel_ids(self) -> List[int]:
        return [int(k) for k, v in self.channels.items() if v.get("enabled")]

    def get_state(self, channel_id: int) -> Dict[str, Any]:
        return self._get(channel_id)

    def get_projects(self, channel_id: int) -> List[Dict[str, Any]]:
        return list(self._get(channel_id).get("projects") or [])

    def set_projects(self, channel_id: int, projects: List[Dict[str, Any]], *, snapshot: bool = False) -> None:
        st = self._get(channel_id)
        if snapshot:
            st["undo_snapshot"] = json.loads(json.dumps(st.get("projects") or []))
        st["projects"] = projects
        self.save()

    def restore_undo(self, channel_id: int) -> bool:
        st = self._get(channel_id)
        snap = st.get("undo_snapshot")
        if not isinstance(snap, list):
            return False
        st["projects"] = snap
        st["undo_snapshot"] = None
        self.save()
        return True

    def register_anchor(self, message_id: int, channel_id: int) -> None:
        self.anchors[str(message_id)] = str(channel_id)
        while len(self.anchors) > MAX_ANCHORS:
            self.anchors.pop(next(iter(self.anchors)))
        self.save()

    def channel_for_anchor(self, message_id: int) -> Optional[int]:
        cid = self.anchors.get(str(message_id))
        return int(cid) if cid else None

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.save_file), exist_ok=True)
        with open(self.save_file, "w") as f:
            json.dump({"channels": self.channels, "anchors": self.anchors}, f, indent=2)

    def _load(self) -> None:
        try:
            with open(self.save_file) as f:
                data = json.load(f)
            self.channels = dict(data.get("channels", {}) or {})
            self.anchors = dict(data.get("anchors", {}) or {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass


notes_manager = NotesManager()

_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
# In-memory triggers; None = unknown after restart (idle sweep catches up once).
_pending_count: Dict[int, Optional[int]] = {}
_last_msg_ts: Dict[int, float] = {}
_idle_loop_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------- merge / render


def merge_updates(projects: List[Dict[str, Any]], updates: List[Any], ts: float) -> List[Dict[str, Any]]:
    """Upsert LLM update objects into projects (by id, then by normalized name)."""
    out = [dict(p) for p in projects]
    by_id = {str(p.get("id")): p for p in out}
    by_name = {_norm(p.get("name", "")): p for p in out}
    for u in updates:
        if not isinstance(u, dict):
            continue
        uid = _clean_str(u.get("id"), 64)
        name = _clean_str(u.get("name"), 80)
        proj = by_id.get(uid)
        if proj is not None and name and _norm(name) != _norm(proj.get("name", "")):
            by_name.pop(_norm(proj.get("name", "")), None)
            by_name[_norm(name)] = proj
            proj["name"] = name  # rename only when matched by id
        if proj is None and name:
            proj = by_name.get(_norm(name))
        if proj is None:
            if not name:
                continue
            base = _slug(name)
            pid, n = base, 2
            while pid in by_id:
                pid, n = f"{base}_{n}", n + 1
            proj = {
                "id": pid,
                "name": name,
                "status": "idea",
                "summary": "",
                "key_points": [],
                "decisions": [],
                "open_questions": [],
                "last_mentioned_ts": ts,
            }
            out.append(proj)
            by_id[pid] = proj
            by_name[_norm(name)] = proj
        status = _clean_status(u.get("status"))
        if status:
            proj["status"] = status
        summary = _clean_str(u.get("summary"))
        if summary:
            proj["summary"] = summary
        resolved = {_norm(x) for x in _clean_list(u.get("resolved_open_questions"))}
        if resolved:
            proj["open_questions"] = [q for q in proj.get("open_questions", []) if _norm(q) not in resolved]
        for field, add_key in (
            ("key_points", "add_key_points"),
            ("decisions", "add_decisions"),
            ("open_questions", "add_open_questions"),
        ):
            items = list(proj.get(field) or [])
            seen = {_norm(x) for x in items}
            for item in _clean_list(u.get(add_key)):
                if _norm(item) not in seen:
                    items.append(item)
                    seen.add(_norm(item))
            proj[field] = items[-LIST_CAPS[field]:]
        proj["last_mentioned_ts"] = max(float(proj.get("last_mentioned_ts") or 0), ts)
    return out


def _is_archived(p: Dict[str, Any], now: float) -> bool:
    return (now - float(p.get("last_mentioned_ts") or 0)) > ARCHIVE_AFTER_SECONDS


def _render_project(p: Dict[str, Any]) -> List[str]:
    lines = [f"## {p.get('name', '?')} [{p.get('status') or 'idea'}]"]
    if p.get("summary"):
        lines.append(p["summary"])
    for label, field in (("Key points", "key_points"), ("Decided", "decisions"), ("Open", "open_questions")):
        items = p.get(field) or []
        if items:
            lines.append(label)
            lines.extend(f"- {x}" for x in items)
    lines.append("")
    return lines


def render_notes(channel_name: str, projects: List[Dict[str, Any]], last_run_ts: float, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    updated = (
        datetime.fromtimestamp(last_run_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if last_run_ts
        else "never"
    )
    lines = [f"# Notes: #{channel_name}", f"Updated {updated}", ""]
    ordered = sorted(projects, key=lambda p: float(p.get("last_mentioned_ts") or 0), reverse=True)
    active = [p for p in ordered if not _is_archived(p, now)]
    archived = [p for p in ordered if _is_archived(p, now)]
    if not projects:
        lines.append("(No projects or products found yet.)")
    for p in active:
        lines.extend(_render_project(p))
    if archived:
        lines.append("════ Archived (no mention in 30+ days) ════")
        lines.append("")
        for p in archived:
            lines.extend(_render_project(p))
    return "\n".join(lines).rstrip() + "\n"


def _projects_context(projects: List[Dict[str, Any]], now: float) -> str:
    rows = []
    for p in projects:
        row: Dict[str, Any] = {"id": p.get("id"), "name": p.get("name"), "status": p.get("status")}
        if _is_archived(p, now):
            row["summary"] = p.get("summary", "")
        else:
            for k in ("summary", "key_points", "decisions", "open_questions"):
                if p.get(k):
                    row[k] = p[k]
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False)


# ---------------------------------------------------------------- LLM


def _model_and_system(user_id: int, prompt_key: str) -> Tuple[str, str, str]:
    from utils.llm_service import get_enhanced_prompt

    eff = model_manager.get_effective_model_for_function(user_id, "notes")
    sys_p = get_enhanced_prompt(prompt_key)
    base = persona_manager.get_persona(get_function_persona_name("notes")).strip()
    full_sys = f"{base}\n\n{sys_p}" if base else sys_p
    return eff.get("model", "qwen2.5:7b"), eff.get("provider", "local"), full_sys


async def _llm_json(user_id: int, prompt_key: str, user_block: str, num_predict: int) -> Optional[Dict[str, Any]]:
    from utils.llm_service import _try_models_with_fallback, _extract_json_object_loose

    model, provider, full_sys = _model_and_system(user_id, prompt_key)
    try:
        _, raw = await _try_models_with_fallback(
            model,
            [{"role": "system", "content": full_sys}, {"role": "user", "content": user_block}],
            images=False,
            provider=provider,
            request_options={"num_predict": num_predict, "temperature": 0.1},
        )
    except Exception:
        return None
    parsed = _extract_json_object_loose(raw or "")
    return parsed or None


async def _llm_update_chunk(user_id: int, projects: List[Dict[str, Any]], transcript: str) -> Optional[List[Any]]:
    user_block = (
        f"existing_projects_json:\n{_projects_context(projects, time.time())}\n\n"
        f"new_messages:\n{transcript}"
    )
    parsed = await _llm_json(user_id, "notes_update", user_block, 1200)
    if parsed is None or not isinstance(parsed.get("updates"), list):
        return None
    return parsed["updates"]


# ---------------------------------------------------------------- update pass


def _format_message(msg: discord.Message) -> Optional[str]:
    if msg.author.bot or getattr(msg, "interaction", None) is not None:
        return None
    if msg.type not in (discord.MessageType.default, discord.MessageType.reply):
        return None
    ref = msg.reference
    if ref and ref.message_id and notes_manager.channel_for_anchor(ref.message_id) is not None:
        return None  # edit instructions for the notes themselves
    text = (msg.clean_content or "").strip()
    if not text:
        return None
    text = _URL_RE.sub(lambda m: f"<link:{m.group(1)}>", text)
    text = re.sub(r"\s+", " ", text)[:MESSAGE_CAP_CHARS]
    name = getattr(msg.author, "display_name", None) or msg.author.name
    return f"{name}: {text}"


async def _resolve_channel(client: discord.Client, channel_id: int):
    ch = client.get_channel(channel_id)
    if ch is None:
        try:
            ch = await client.fetch_channel(channel_id)
        except Exception:
            return None
    return ch


async def run_update(client: discord.Client, channel_id: int) -> int:
    """Process unread history for a channel. Returns number of messages read (0 if none or failed)."""
    lock = _locks[channel_id]
    async with lock:
        channel = await _resolve_channel(client, channel_id)
        if channel is None:
            return 0
        st = notes_manager.get_state(channel_id)
        user_id = int(st.get("enabled_by") or 0)
        last_id = int(st.get("last_processed_message_id") or 0)
        after = discord.Object(id=last_id) if last_id else None

        chunk: List[str] = []
        chunk_chars = 0
        chunk_last_id = 0
        chunk_last_ts = 0.0
        seen_last_id = 0
        read = 0

        async def _flush() -> bool:
            nonlocal chunk, chunk_chars
            if chunk:
                updates = await _llm_update_chunk(user_id, notes_manager.get_projects(channel_id), "\n".join(chunk))
                if updates is None:
                    return False
                if updates:
                    merged = merge_updates(notes_manager.get_projects(channel_id), updates, chunk_last_ts)
                    notes_manager.get_state(channel_id)["projects"] = merged
            notes_manager.get_state(channel_id)["last_processed_message_id"] = chunk_last_id or seen_last_id
            notes_manager.save()
            chunk, chunk_chars = [], 0
            return True

        try:
            async for msg in channel.history(limit=None, after=after, oldest_first=True):
                read += 1
                seen_last_id = msg.id
                line = _format_message(msg)
                if line is None:
                    if not chunk:
                        chunk_last_id = msg.id
                    continue
                if chunk and chunk_chars + len(line) > CHUNK_CHARS:
                    if not await _flush():
                        return 0
                chunk.append(line)
                chunk_chars += len(line) + 1
                chunk_last_id = msg.id
                chunk_last_ts = msg.created_at.timestamp()
        except (discord.Forbidden, discord.HTTPException):
            pass
        if read and not await _flush():
            return 0
        if seen_last_id and int(notes_manager.get_state(channel_id).get("last_processed_message_id") or 0) < seen_last_id:
            notes_manager.get_state(channel_id)["last_processed_message_id"] = seen_last_id
        notes_manager.get_state(channel_id)["last_run_ts"] = time.time()
        notes_manager.save()
        _pending_count[channel_id] = 0
        return read


def _spawn_update(client: discord.Client, channel_id: int) -> None:
    if _locks[channel_id].locked():
        return
    dm_background.spawn(run_update(client, channel_id), name=f"notes_{channel_id}")


def on_guild_message(client: discord.Client, message: discord.Message) -> None:
    """Cheap per-message hook: count pending messages and trigger a batch at the threshold."""
    cid = message.channel.id
    if message.author.bot or not notes_manager.is_enabled(cid):
        return
    _last_msg_ts[cid] = time.time()
    count = (_pending_count.get(cid) or 0) + 1
    _pending_count[cid] = count
    if count >= PENDING_THRESHOLD and is_bot_awake():
        _pending_count[cid] = 0
        _spawn_update(client, cid)


async def _idle_loop(client: discord.Client) -> None:
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            if is_bot_awake():
                now = time.time()
                for cid in notes_manager.enabled_channel_ids():
                    pending = _pending_count.get(cid)
                    quiet = (now - _last_msg_ts.get(cid, 0.0)) >= IDLE_SECONDS
                    if pending is None or (pending > 0 and quiet):
                        _pending_count[cid] = 0
                        _spawn_update(client, cid)
        except Exception:
            pass
        await asyncio.sleep(LOOP_INTERVAL_SECONDS)


def start_idle_loop(client: discord.Client) -> None:
    global _idle_loop_task
    if _idle_loop_task is not None and not _idle_loop_task.done():
        return
    _idle_loop_task = asyncio.create_task(_idle_loop(client), name="notes_idle_loop")


# ---------------------------------------------------------------- edit pass


async def apply_edit(channel_id: int, user_id: int, instruction: str, *, is_file: bool) -> Tuple[bool, str]:
    projects = notes_manager.get_projects(channel_id)
    full = [
        {k: p.get(k) for k in ("id", "name", "status", "summary", "key_points", "decisions", "open_questions")}
        for p in projects
    ]
    if is_file:
        request = (
            "The user edited the rendered notes file below. Make the stored projects match it "
            "(sections, entries, statuses and text). Ignore the header and the Archived divider.\n\n"
            + instruction[:20000]
        )
    else:
        request = instruction[:4000]
    user_block = f"current_projects_json:\n{json.dumps(full, ensure_ascii=False)}\n\nedit_request:\n{request}"
    parsed = await _llm_json(user_id, "notes_edit", user_block, 3000)
    if parsed is None or not isinstance(parsed.get("projects"), list):
        return False, "Could not apply that edit (model returned no valid notes). Nothing changed."

    old_by_id = {str(p.get("id")): p for p in projects}
    now = time.time()
    new_projects: List[Dict[str, Any]] = []
    used: set = set()
    for item in parsed["projects"]:
        if not isinstance(item, dict):
            continue
        name = _clean_str(item.get("name"), 80)
        if not name:
            continue
        pid = _clean_str(item.get("id"), 64) or _slug(name)
        while pid in used:
            pid += "_2"
        used.add(pid)
        old = old_by_id.get(pid, {})
        new_projects.append(
            {
                "id": pid,
                "name": name,
                "status": _clean_status(item.get("status")) or old.get("status") or "idea",
                "summary": _clean_str(item.get("summary")),
                "key_points": _clean_list(item.get("key_points"))[-LIST_CAPS["key_points"]:],
                "decisions": _clean_list(item.get("decisions"))[-LIST_CAPS["decisions"]:],
                "open_questions": _clean_list(item.get("open_questions"))[-LIST_CAPS["open_questions"]:],
                "last_mentioned_ts": float(old.get("last_mentioned_ts") or now),
            }
        )
    notes_manager.set_projects(channel_id, new_projects, snapshot=True)
    return True, "✅ Notes updated."


# ---------------------------------------------------------------- Discord output


def build_notes_payload(channel_id: int, channel_name: str) -> Tuple[discord.Embed, discord.File]:
    st = notes_manager.get_state(channel_id)
    projects = list(st.get("projects") or [])
    last_run = float(st.get("last_run_ts") or 0)
    body = render_notes(channel_name, projects, last_run)
    attachment = discord.File(io.BytesIO(body.encode("utf-8")), filename=NOTES_EXPORT_FILENAME)
    now = time.time()
    archived = sum(1 for p in projects if _is_archived(p, now))
    embed = discord.Embed(
        title=f"Notes · #{channel_name}",
        description="Reply with edits, or attach an edited .txt of the notes",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="On", value="yes" if st.get("enabled") else "no", inline=True)
    embed.add_field(name="Projects", value=str(len(projects) - archived), inline=True)
    embed.add_field(name="Archived", value=str(archived), inline=True)
    embed.add_field(name="Last update", value=f"<t:{int(last_run)}:R>" if last_run else "never", inline=True)
    return embed, attachment


class NotesRevertView(discord.ui.View):
    def __init__(self, channel_id: int, channel_name: str, timeout: float = 900):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id
        self.channel_name = channel_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from whitelist import get_user_permission

        if get_user_permission(interaction.user.id):
            return True
        await interaction.response.send_message("❌ Denied", ephemeral=True)
        return False

    @discord.ui.button(label="Revert", style=discord.ButtonStyle.secondary)
    async def revert(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not notes_manager.restore_undo(self.channel_id):
            await interaction.response.send_message("Nothing to revert.", ephemeral=True)
            return
        button.disabled = True
        embed, attachment = build_notes_payload(self.channel_id, self.channel_name)
        await interaction.response.edit_message(content="↩️ Reverted.", embed=embed, attachments=[attachment], view=self)
        self.stop()


async def _read_first_txt_attachment(message: discord.Message) -> Optional[str]:
    for att in list(getattr(message, "attachments", []) or []):
        if not (att.filename or "").lower().endswith(".txt"):
            continue
        try:
            raw = await att.read()
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    return None


async def try_handle_notes_reply(client: discord.Client, message: discord.Message) -> bool:
    """Reply to a /notes message → apply edit to that channel's notes. Returns True if handled."""
    ref = message.reference
    if not ref or not ref.message_id:
        return False
    notes_cid = notes_manager.channel_for_anchor(ref.message_id)
    if notes_cid is None:
        return False

    file_body = await _read_first_txt_attachment(message)
    text = (file_body if file_body is not None else (message.content or "")).strip()
    if not text:
        await message.reply("Reply with an edit (e.g. `remove X`) or attach an edited `.txt` of the notes.")
        return True

    notes_ch = await _resolve_channel(client, notes_cid)
    channel_name = getattr(notes_ch, "name", str(notes_cid))
    async with message.channel.typing():
        async with _locks[notes_cid]:
            ok, status = await apply_edit(notes_cid, message.author.id, text, is_file=file_body is not None)
    if not ok:
        await message.reply(f"❌ {status}")
        return True
    embed, attachment = build_notes_payload(notes_cid, channel_name)
    sent = await message.reply(status, embed=embed, file=attachment, view=NotesRevertView(notes_cid, channel_name))
    notes_manager.register_anchor(sent.id, notes_cid)
    return True
