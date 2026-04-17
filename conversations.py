import json
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from config import get_chat_history


DM_SESSION_GAP_SECONDS = 36 * 3600  # treat as fresh session after this idle (unless reply-to-bot)
_TOPIC_STALE_SECONDS = 30 * 86400  # forget topic summaries not touched in ~30 days
_DM_TOPICS_MAX = 14

# Strip embedded "recent channel" block from persisted user lines (see ask_llm / Discord context).
_DISCORD_RECENT_CTX_RE = re.compile(
    r"Recent messages in this channel:\n[\s\S]*?\n[\w.\- ]+ says:\s*",
    re.IGNORECASE,
)


def strip_discord_recent_context_block(content: str) -> str:
    return _DISCORD_RECENT_CTX_RE.sub("", (content or "").strip()).strip()


def is_news_style_dm_bot_text(content: str) -> bool:
    """Heuristic: automated news DM body (compact article, digest header, slop edit)."""
    core = strip_discord_recent_context_block(content)
    if not core:
        return False
    low = core.lower()
    if "⏰ **you're outside your daily quiet window**" in low:
        return True
    if "news briefing" in low and "quiet" in low:
        return True
    if "**slop**" in low and "~~" in core:
        return True
    if core.lstrip().startswith("# **") and "source:" in low:
        if "http://" in low or "https://" in low:
            return True
    return False


def is_news_style_embed_title(title: str) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return False
    if "news briefing" in t:
        return True
    if "quiet hours summary" in t:
        return True
    return False


def is_slash_command_bot_turn(meta: Optional[Dict[str, Any]]) -> bool:
    """True when this assistant line came from a Discord slash-command reply."""
    if not meta or not isinstance(meta, dict):
        return False
    return bool(meta.get("discord_interaction") and (meta.get("command_name") or "").strip())


class ConversationManager:
    """One thread per channel. Reply to bot = continue that chat; wake word or /chat = new chat."""

    def __init__(self, save_file="data/conversations.json"):
        self.max_history = get_chat_history()
        self.save_file = save_file
        self.conversations = defaultdict(list)
        self.last_bot_message = {}
        self.recent_bot_message_ids = defaultdict(list)  # per channel, last 10 bot message ids (in-memory only)
        self.dm_history_cutoff = {}
        self.dm_summaries = defaultdict(list)  # legacy flat summaries (migrated to dm_topics)
        self.dm_topics = defaultdict(list)  # list of dicts: id, label, summary, last_ts
        self.dm_profile_llm = {}  # channel_id -> brief text (LLM-built; separate from adaptive heuristics)
        self.dm_last_user_ts = {}  # channel_id -> last user message unix time (for session gap)
        self.dm_adaptive_user_id = {}  # channel_id -> int user id when DM is adaptive (for background tasks)
        self.dm_fast_reply_until = {}
        self._load()

    def set_max_history(self, n: int):
        """Update max history (e.g. after /chat-history). Next add_message will trim to new limit."""
        self.max_history = max(1, min(100, n))

    def _key(self, channel_id):
        return str(channel_id)

    def is_continuation(self, message):
        if not message.reference or not message.reference.message_id:
            return False
        key = self._key(message.channel.id)
        ref_id = message.reference.message_id
        recent = self.recent_bot_message_ids.get(key, [])
        last = self.last_bot_message.get(key)
        return ref_id == last or ref_id in recent

    def should_continue_dm_session(self, channel_id: int, is_reply_to_bot: bool) -> bool:
        """DM: continue rolling context unless long idle gap (reply-to-bot always continues)."""
        if is_reply_to_bot:
            return True
        key = self._key(channel_id)
        last = self.dm_last_user_ts.get(key)
        if last is None:
            return True
        return (time.time() - float(last)) < DM_SESSION_GAP_SECONDS

    def touch_dm_user_activity(self, channel_id: int) -> None:
        self.dm_last_user_ts[self._key(channel_id)] = time.time()

    def get_dm_last_user_activity(self, channel_id: int):
        return self.dm_last_user_ts.get(self._key(channel_id))

    def set_dm_adaptive_user(self, channel_id: int, user_id: int) -> None:
        self.dm_adaptive_user_id[self._key(channel_id)] = int(user_id)

    def get_dm_adaptive_user(self, channel_id: int):
        raw = self.dm_adaptive_user_id.get(self._key(channel_id))
        return int(raw) if raw is not None else None

    def get_dm_profile_llm(self, channel_id: int) -> str:
        return str(self.dm_profile_llm.get(self._key(channel_id), "") or "").strip()

    def set_dm_profile_llm(self, channel_id: int, text: str) -> None:
        key = self._key(channel_id)
        t = (text or "").strip()
        if len(t) > 1200:
            t = t[:1200].rstrip()
        self.dm_profile_llm[key] = t

    def get_dm_topics(self, channel_id: int):
        return list(self.dm_topics.get(self._key(channel_id), []) or [])

    def set_dm_topics(self, channel_id: int, topics: list) -> None:
        key = self._key(channel_id)
        cleaned = []
        now = time.time()
        for item in topics or []:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("id", "") or "").strip() or f"t{len(cleaned)}"
            label = str(item.get("label", "") or "").strip()[:120]
            summary = str(item.get("summary", "") or "").strip()
            if not summary and not label:
                continue
            try:
                last_ts = float(item.get("last_ts", now))
            except (TypeError, ValueError):
                last_ts = now
            if now - last_ts > _TOPIC_STALE_SECONDS:
                continue
            cleaned.append(
                {
                    "id": tid[:64],
                    "label": label or tid[:40],
                    "summary": summary[:900],
                    "last_ts": last_ts,
                }
            )
        self.dm_topics[key] = cleaned[-_DM_TOPICS_MAX:]

    def prune_stale_dm_topics(self, channel_id: int) -> None:
        """Drop topics older than stale window."""
        now = time.time()
        key = self._key(channel_id)
        items = self.get_dm_topics(channel_id)
        kept = [t for t in items if now - float(t.get("last_ts", 0)) <= _TOPIC_STALE_SECONDS]
        if len(kept) != len(items):
            self.dm_topics[key] = kept[-_DM_TOPICS_MAX:]

    def add_message(
        self,
        channel_id,
        role,
        content,
        *,
        meta: Optional[Dict[str, Any]] = None,
    ):
        key = self._key(channel_id)
        entry: Dict[str, Any] = {"role": role, "content": content}
        if meta:
            entry["meta"] = dict(meta)
        self.conversations[key].append(entry)
        if len(self.conversations[key]) > self.max_history * 2:
            self.conversations[key] = self.conversations[key][-self.max_history * 2 :]

    def get_conversation(self, channel_id):
        return self.conversations.get(self._key(channel_id), [])

    def roll_adaptive_dm_transcript_messages(
        self, channel_id, messages: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """User/assistant turns for adaptive DM LLM history: drop slash-command bot replies and news bot posts."""
        raw = messages if messages is not None else self.get_conversation(channel_id)
        out: List[Dict[str, Any]] = []
        for m in raw or []:
            if not isinstance(m, dict):
                continue
            if m.get("role") not in ("user", "assistant"):
                continue
            meta = m.get("meta") if isinstance(m.get("meta"), dict) else None
            if m.get("role") == "assistant":
                if is_slash_command_bot_turn(meta):
                    continue
                if meta and meta.get("news_delivery"):
                    continue
                if is_news_style_dm_bot_text(str(m.get("content", "") or "")):
                    continue
            out.append(m)
        return out

    def replace_conversation(self, channel_id, messages):
        key = self._key(channel_id)
        self.conversations[key] = list(messages or [])

    def reset_dm_transcript_only(self, channel_id: int) -> None:
        """Clear rolling DM messages only; keep topic summaries and LLM profile memory."""
        key = self._key(channel_id)
        self.conversations[key] = []
        self.last_bot_message.pop(key, None)
        self.recent_bot_message_ids.pop(key, None)

    def clear_conversation(self, channel_id=None, user_id=None):
        if channel_id is not None:
            key = self._key(channel_id)
            self.conversations.pop(key, None)
            self.last_bot_message.pop(key, None)
            self.recent_bot_message_ids.pop(key, None)
            self.dm_summaries.pop(key, None)
            self.dm_topics.pop(key, None)
            self.dm_profile_llm.pop(key, None)
            self.dm_last_user_ts.pop(key, None)
            self.dm_adaptive_user_id.pop(key, None)
            self.dm_fast_reply_until.pop(key, None)
        elif user_id is not None:
            self.conversations.clear()
            self.last_bot_message.clear()
            self.recent_bot_message_ids.clear()
            self.dm_summaries.clear()
            self.dm_history_cutoff.clear()
            self.dm_fast_reply_until.clear()
            self.dm_topics.clear()
            self.dm_profile_llm.clear()
            self.dm_last_user_ts.clear()
            self.dm_adaptive_user_id.clear()

    def get_dm_history_cutoff(self, channel_id, default_cutoff=10):
        key = self._key(channel_id)
        raw = self.dm_history_cutoff.get(key, default_cutoff)
        try:
            return max(4, min(80, int(raw)))
        except (TypeError, ValueError):
            return default_cutoff

    def set_dm_history_cutoff(self, channel_id, cutoff):
        key = self._key(channel_id)
        self.dm_history_cutoff[key] = max(4, min(80, int(cutoff)))

    def append_dm_summary(self, channel_id, summary_text, merged_messages=0):
        key = self._key(channel_id)
        entries = self.dm_summaries[key]
        entries.append(
            {
                "summary": str(summary_text or "").strip(),
                "merged_messages": int(merged_messages or 0),
            }
        )
        self.dm_summaries[key] = entries[-8:]

    def get_dm_summaries(self, channel_id):
        return self.dm_summaries.get(self._key(channel_id), [])

    def get_dm_summary_text(self, channel_id):
        """Compact text block for LLM: time-decayed topic lines + optional legacy bullets."""
        self.prune_stale_dm_topics(channel_id)
        topics = self.get_dm_topics(channel_id)
        now = time.time()
        lines = []
        for t in sorted(topics, key=lambda x: float(x.get("last_ts", 0)), reverse=True):
            label = str(t.get("label", "") or "").strip()
            summary = str(t.get("summary", "") or "").strip()
            if not summary:
                continue
            age_days = max(0.0, (now - float(t.get("last_ts", now))) / 86400.0)
            if age_days > 21:
                cap = 80
            elif age_days > 7:
                cap = 140
            elif age_days > 2:
                cap = 220
            else:
                cap = 320
            if len(summary) > cap:
                summary = summary[: cap - 1].rstrip() + "…"
            ts = int(t.get("last_ts", now))
            prefix = label[:60] if label else "topic"
            lines.append(f"- [{prefix}, last={ts}] {summary}")

        legacy = self.get_dm_summaries(channel_id)
        for item in legacy[-2:]:
            text = str(item.get("summary", "")).strip()
            if text:
                if len(text) > 200:
                    text = text[:199] + "…"
                lines.append(f"- [archive] {text}")

        return "\n".join(lines[:12])

    def set_dm_fast_reply_window(self, channel_id, minutes: int):
        key = self._key(channel_id)
        mins = max(1, min(240, int(minutes)))
        self.dm_fast_reply_until[key] = time.time() + (mins * 60)

    def clear_dm_fast_reply_window(self, channel_id):
        self.dm_fast_reply_until.pop(self._key(channel_id), None)

    def get_dm_fast_reply_remaining_seconds(self, channel_id) -> int:
        key = self._key(channel_id)
        until = self.dm_fast_reply_until.get(key)
        if until is None:
            return 0
        remaining = int(until - time.time())
        if remaining <= 0:
            self.dm_fast_reply_until.pop(key, None)
            return 0
        return remaining

    def is_dm_fast_reply_active(self, channel_id) -> bool:
        return self.get_dm_fast_reply_remaining_seconds(channel_id) > 0

    def set_last_bot_message(self, channel_id, message_id):
        key = self._key(channel_id)
        self.last_bot_message[key] = message_id
        ids = self.recent_bot_message_ids[key]
        if message_id not in ids:
            ids.append(message_id)
        self.recent_bot_message_ids[key] = ids[-10:]

    def save(self):
        with open(self.save_file, "w") as f:
            json.dump(
                {
                    "conversations": dict(self.conversations),
                    "last_bot_message": self.last_bot_message,
                    "dm_history_cutoff": self.dm_history_cutoff,
                    "dm_summaries": dict(self.dm_summaries),
                    "dm_topics": dict(self.dm_topics),
                    "dm_profile_llm": dict(self.dm_profile_llm),
                    "dm_last_user_ts": dict(self.dm_last_user_ts),
                    "dm_adaptive_user_id": dict(self.dm_adaptive_user_id),
                    "dm_fast_reply_until": self.dm_fast_reply_until,
                },
                f,
                indent=2,
            )

    def _migrate_legacy_summaries(self) -> None:
        """One-time: fold legacy dm_summaries into dm_topics so nothing is lost."""
        dirty = False
        now = time.time()
        for key, entries in list(self.dm_summaries.items()):
            if not entries:
                continue
            existing = self.get_dm_topics(int(key)) if key.isdigit() else self.dm_topics.get(key, [])
            if existing:
                continue
            topics = []
            for i, item in enumerate(entries[-6:]):
                text = str(item.get("summary", "") or "").strip()
                if not text:
                    continue
                topics.append(
                    {
                        "id": f"mig{i}",
                        "label": "prior DM",
                        "summary": text[:800],
                        "last_ts": now - (len(entries) - i) * 60.0,
                    }
                )
            if topics:
                self.dm_topics[key] = topics[-_DM_TOPICS_MAX:]
                self.dm_summaries[key] = []
                dirty = True
        if dirty:
            self.save()

    def _load(self):
        try:
            with open(self.save_file) as f:
                data = json.load(f)
                self.conversations = defaultdict(list, data.get("conversations", {}))
                self.last_bot_message = data.get("last_bot_message", {})
                self.dm_history_cutoff = data.get("dm_history_cutoff", {})
                self.dm_summaries = defaultdict(list, data.get("dm_summaries", {}))
                self.dm_fast_reply_until = data.get("dm_fast_reply_until", {})
                self.dm_topics = defaultdict(list, data.get("dm_topics", {}))
                self.dm_profile_llm = dict(data.get("dm_profile_llm", {}))
                self.dm_last_user_ts = dict(data.get("dm_last_user_ts", {}))
                self.dm_adaptive_user_id = dict(data.get("dm_adaptive_user_id", {}))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        self._migrate_legacy_summaries()
        self._one_time_clear_transcripts_keep_dm_memory()

    def _one_time_clear_transcripts_keep_dm_memory(self) -> None:
        """
        Clear persisted rolling transcripts once; keep DM summaries/topics/profile/cutoffs.
        Legacy flat summaries are migrated into dm_topics before dm_summaries is cleared.
        """
        marker = "data/.dm_transcript_purge_v1"
        try:
            with open(marker) as f:
                if (f.read() or "").strip():
                    return
        except FileNotFoundError:
            pass
        self.conversations = defaultdict(list)
        self.last_bot_message = {}
        self.recent_bot_message_ids = defaultdict(list)
        # Legacy bullets now live in topics; drop duplicate storage.
        self.dm_summaries = defaultdict(list)
        try:
            with open(marker, "w") as f:
                f.write("1\n")
        except OSError:
            pass
        self.save()


conversation_manager = ConversationManager()
