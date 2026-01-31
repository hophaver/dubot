from commands.base import CommandBase


class HACommands(CommandBase):
    def register(self):
        from . import himas, explain, listentities, removeentity, ha_status, find_sensor
        himas.register(self.client)
        explain.register(self.client)
        listentities.register(self.client)
        removeentity.register(self.client)
        ha_status.register(self.client)
        find_sensor.register(self.client)
