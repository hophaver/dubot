from commands.base import CommandBase


class ModelCommands(CommandBase):
    def register(self):
        from . import model, pull_model
        model.register(self.client)
        pull_model.register(self.client)
