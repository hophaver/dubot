from . import status
from . import checkwake
from . import bal


class GeneralCommands:
    def __init__(self, client):
        self.client = client

    def register(self):
        status.register(self.client)
        checkwake.register(self.client)
        bal.register(self.client)
