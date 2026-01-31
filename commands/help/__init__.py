from commands.base import CommandBase


class HelpCommands(CommandBase):
    def register(self):
        from . import help as help_cmd
        help_cmd.register(self.client)
