from commands.base import CommandBase


class PersonaCommands(CommandBase):
    def register(self):
        from . import persona_create

        persona_create.register(self.client)
