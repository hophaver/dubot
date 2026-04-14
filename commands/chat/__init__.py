from commands.base import CommandBase


class ChatCommands(CommandBase):
    def register(self):
        from . import chat, forget, chat_history, conversation, conversation_frequency, dm_history, jarvis
        chat.register(self.client)
        forget.register(self.client)
        chat_history.register(self.client)
        dm_history.register(self.client)
        jarvis.register(self.client)
        conversation.register(self.client)
        conversation_frequency.register(self.client)
