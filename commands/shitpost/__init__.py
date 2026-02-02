from commands.base import CommandBase
from .trigger import handle_shitpost
from . import ignore


class ShitpostCommands(CommandBase):
    def register(self):
        ignore.register(self.client)


__all__ = ["handle_shitpost", "ShitpostCommands"]
