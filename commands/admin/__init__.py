from commands.base import CommandBase


class AdminCommands(CommandBase):
    def register(self):
        from . import update, rollback, purge, restart, kill, whitelist, setwake, sethome, setstatus, clone, profanity, remover
        update.register(self.client)
        rollback.register(self.client)
        purge.register(self.client)
        restart.register(self.client)
        kill.register(self.client)
        whitelist.register(self.client)
        setwake.register(self.client)
        sethome.register(self.client)
        setstatus.register(self.client)
        clone.register(self.client)
        profanity.register(self.client)
        remover.register(self.client)
