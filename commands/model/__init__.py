from commands.base import CommandBase


class ModelCommands(CommandBase):
    def register(self):
        from . import llm_settings, pull_model

        llm_settings.register(self.client)
        pull_model.register(self.client)
