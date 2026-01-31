from commands.base import CommandBase


class PersonaCommands(CommandBase):
    def register(self):
        from . import persona, persona_create
        persona.register(self.client)
        persona_create.register(self.client)
