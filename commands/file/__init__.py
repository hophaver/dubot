from commands.base import CommandBase


class FileCommands(CommandBase):
    def register(self):
        from . import analyze, examine, interrogate, code_review, ocr, compare_files
        analyze.register(self.client)
        examine.register(self.client)
        interrogate.register(self.client)
        code_review.register(self.client)
        ocr.register(self.client)
        compare_files.register(self.client)
