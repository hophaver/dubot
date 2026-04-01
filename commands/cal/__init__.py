from commands.base import CommandBase


class CalCommands(CommandBase):
    def register(self):
        from . import cal

        cal.register(self.client)
