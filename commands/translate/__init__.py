from commands.base import CommandBase


class TranslateCommands(CommandBase):
    def register(self):
        from . import translate
        translate.register(self.client)
