from commands.base import CommandBase


class NewsCommands(CommandBase):
    def register(self):
        from . import news, news_model, news_time, news_source
        news.register(self.client)
        news_model.register(self.client)
        news_time.register(self.client)
        news_source.register(self.client)
