from commands.base import CommandBase


class ChatCommands(CommandBase):
    def register(self):
        from . import chat, forget
        chat.register(self.client)
        forget.register(self.client)
