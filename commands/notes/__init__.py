from commands.base import CommandBase


class NotesCommands(CommandBase):
    def register(self):
        from . import notes

        notes.register(self.client)
