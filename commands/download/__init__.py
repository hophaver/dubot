from commands.base import CommandBase


class DownloadCommands(CommandBase):
    def register(self):
        from . import download, download_limit
        download.register(self.client)
        download_limit.register(self.client)
