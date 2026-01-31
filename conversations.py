import json
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

    def clear_conversation(self, channel_id=None, user_id=None):
        if channel_id is not None:
            key = self._key(channel_id)
            self.conversations.pop(key, None)
            self.last_bot_message.pop(key, None)
            self.recent_bot_message_ids.pop(key, None)
        elif user_id is not None:
            self.conversations.clear()
            self.last_bot_message.clear()
            self.recent_bot_message_ids.clear()

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
                {"conversations": dict(self.conversations), "last_bot_message": self.last_bot_message},
                f,
                indent=2,
            )

    def _load(self):
        try:
            with open(self.save_file) as f:
                data = json.load(f)
                self.conversations = defaultdict(list, data.get("conversations", {}))
                self.last_bot_message = data.get("last_bot_message", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass


conversation_manager = ConversationManager()
