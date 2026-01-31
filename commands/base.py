class CommandBase:
    def __init__(self, client):
        self.client = client

    def register(self):
        raise NotImplementedError("Subclasses must implement register()")
