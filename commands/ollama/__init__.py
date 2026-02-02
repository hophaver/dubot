from commands.base import CommandBase
from . import control


class OllamaCommands(CommandBase):
    def register(self):
        control.register(self.client)
