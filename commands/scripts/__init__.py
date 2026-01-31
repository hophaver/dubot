from commands.base import CommandBase


class ScriptsCommands(CommandBase):
    def register(self):
        from . import scripts, run
        scripts.register(self.client)
        run.register(self.client)
