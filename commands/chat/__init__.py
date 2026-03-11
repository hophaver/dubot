from commands.base import CommandBase


class ChatCommands(CommandBase):
    def register(self):
        from . import chat, forget, chat_history, conversation, conversation_frequency
        chat.register(self.client)
        forget.register(self.client)
        chat_history.register(self.client)
        conversation.register(self.client)
        conversation_frequency.register(self.client)
