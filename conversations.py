import json
from collections import defaultdict


class ConversationManager:
    """One thread per channel; continues when replying to the bot."""

    def __init__(self, max_history=20, save_file="data/conversations.json"):
        self.max_history = max_history
        self.save_file = save_file
        self.conversations = defaultdict(list)
        self.last_bot_message = {}
        self._load()

    def _key(self, channel_id):
        return str(channel_id)

    def is_continuation(self, message):
        if not message.reference or not message.reference.message_id:
            return False
        return self.last_bot_message.get(self._key(message.channel.id)) == message.reference.message_id

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
        elif user_id is not None:
            self.conversations.clear()
            self.last_bot_message.clear()

    def set_last_bot_message(self, channel_id, message_id):
        self.last_bot_message[self._key(channel_id)] = message_id

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


conversation_manager = ConversationManager(max_history=20)
