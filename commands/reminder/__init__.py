from commands.base import CommandBase


class ReminderCommands(CommandBase):
    def register(self):
        from . import remind, reminders, cancel_reminder
        remind.register(self.client)
        reminders.register(self.client)
        cancel_reminder.register(self.client)
