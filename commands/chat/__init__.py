from commands.base import CommandBase


class ChatCommands(CommandBase):
    def register(self):
        from . import (
            chat,
            forget,
            chat_history,
            conversation,
            conversation_frequency,
            dm_history,
            adaptive,
            adaptive_tune,
            adaptive_tune_batch,
            adaptive_status,
            adaptive_tune_channel,
            fast_reply,
        )

        chat.register(self.client)
        forget.register(self.client)
        chat_history.register(self.client)
        dm_history.register(self.client)
        adaptive.register(self.client)
        adaptive_tune.register(self.client)
        adaptive_tune_batch.register(self.client)
        adaptive_status.register(self.client)
        adaptive_tune_channel.register(self.client)
        fast_reply.register(self.client)
        conversation.register(self.client)
        conversation_frequency.register(self.client)
