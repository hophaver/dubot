from commands.base import CommandBase


class AdminCommands(CommandBase):
    def register(self):
        from . import update, purge, restart, kill, whitelist, setwake, sethome, setstatus
        update.register(self.client)
        purge.register(self.client)
        restart.register(self.client)
        kill.register(self.client)
        whitelist.register(self.client)
        setwake.register(self.client)
        sethome.register(self.client)
        setstatus.register(self.client)
