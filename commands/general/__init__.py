from . import status
from . import checkwake
from . import bal
from . import openrouter_check
from . import cursor
from . import sleep_mode
from . import reliability
from . import trader_slash


class GeneralCommands:
    def __init__(self, client):
        self.client = client

    def register(self):
        status.register(self.client)
        checkwake.register(self.client)
        bal.register(self.client)
        openrouter_check.register(self.client)
        cursor.register(self.client)
        sleep_mode.register(self.client)
        reliability.register(self.client)
        trader_slash.register(self.client)
