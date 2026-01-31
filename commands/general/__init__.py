from . import status
from . import checkwake


class GeneralCommands:
    def __init__(self, client):
        self.client = client

    def register(self):
        status.register(self.client)
        checkwake.register(self.client)
