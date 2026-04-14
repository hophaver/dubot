import json
import time
from collections import defaultdict

from config import get_chat_history


class ConversationManager:
    """One thread per channel. Reply to bot = continue that chat; wake word or /chat = new chat."""

    def __init__(self, save_file="data/conversations.json"):
        self.max_history = get_chat_history()
        self.save_file = save_file
        self.conversations = defaultdict(list)
        self.last_bot_message = {}
        self.recent_bot_message_ids = defaultdict(list)  # per channel, last 10 bot message ids (in-memory only)
        self.dm_history_cutoff = {}
        self.dm_summaries = defaultdict(list)
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

    def add_message(self, channel_id, role, content):
        key = self._key(channel_id)
        self.conversations[key].append({"role": role, "content": content})
        if len(self.conversations[key]) > self.max_history * 2:
            self.conversations[key] = self.conversations[key][-self.max_history * 2 :]

    def get_conversation(self, channel_id):
        return self.conversations.get(self._key(channel_id), [])

    def replace_conversation(self, channel_id, messages):
        key = self._key(channel_id)
        self.conversations[key] = list(messages or [])

    def clear_conversation(self, channel_id=None, user_id=None):
        if channel_id is not None:
            key = self._key(channel_id)
            self.conversations.pop(key, None)
            self.last_bot_message.pop(key, None)
            self.recent_bot_message_ids.pop(key, None)
            self.dm_summaries.pop(key, None)
            self.dm_fast_reply_until.pop(key, None)
        elif user_id is not None:
            self.conversations.clear()
            self.last_bot_message.clear()
            self.recent_bot_message_ids.clear()
            self.dm_summaries.clear()
            self.dm_history_cutoff.clear()
            self.dm_fast_reply_until.clear()

    def get_dm_history_cutoff(self, channel_id, default_cutoff=16):
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
        items = self.get_dm_summaries(channel_id)
        if not items:
            return ""
        lines = []
        for idx, item in enumerate(items[-3:], start=1):
            text = str(item.get("summary", "")).strip()
            if text:
                lines.append(f"{idx}. {text}")
        return "\n".join(lines)

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
                    "dm_fast_reply_until": self.dm_fast_reply_until,
                },
                f,
                indent=2,
            )

    def _load(self):
        try:
            with open(self.save_file) as f:
                data = json.load(f)
                self.conversations = defaultdict(list, data.get("conversations", {}))
                self.last_bot_message = data.get("last_bot_message", {})
                self.dm_history_cutoff = data.get("dm_history_cutoff", {})
                self.dm_summaries = defaultdict(list, data.get("dm_summaries", {}))
                self.dm_fast_reply_until = data.get("dm_fast_reply_until", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass


conversation_manager = ConversationManager()
