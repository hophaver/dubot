"""Command catalogue used by /help and the LLM system prompt.

Populated from the registered slash-command tree after startup, so it can never list commands that
don't exist (or miss ones that do). Only categories and aliases are maintained here.
"""
from typing import Dict, Iterable, List, Optional

# Natural-language aliases that map onto a command for /help lookup and suggestions.
ALIASES: Dict[str, List[str]] = {
    "help": ["commands", "what can you do"],
    "chat": ["talk", "ask"],
    "forget": ["clear", "reset", "clear-chat"],
    "status": ["info", "stats"],
    "bal": ["balance", "openrouter", "credits"],
    "whitelist": ["permissions", "users"],
    "himas": ["home", "lights", "smart home"],
    "explain": ["add", "map"],
    "remind": ["reminder"],
    "reminders": ["my reminders"],
    "llm-settings": ["llm", "persona", "personas", "model", "models", "currentmodel"],
    "download": ["dl"],
    "news": ["subscribe", "news feed"],
    "news-time": ["quiet time", "pause news"],
}


class CommandDatabase:
    def __init__(self):
        self.commands: Dict[str, Dict] = {}
        self.categories: Dict[str, List[str]] = {}
        self._formatted: Optional[str] = None

    def add_command(self, name: str, description: str, category: str = "General"):
        self.commands[name] = {
            "name": name,
            "description": description,
            "category": category,
            "aliases": list(ALIASES.get(name, [])),
        }
        self.categories.setdefault(category, []).append(name)
        self._formatted = None

    def populate(self, entries: Iterable[tuple]) -> None:
        """Replace contents with (name, description, category) tuples."""
        self.commands.clear()
        self.categories.clear()
        for name, description, category in entries:
            self.add_command(name, description, category)

    def get_command(self, name: str) -> Optional[Dict]:
        """Get command info by name or alias"""
        if name in self.commands:
            return self.commands[name]
        for cmd_info in self.commands.values():
            if name in cmd_info["aliases"]:
                return cmd_info
        return None

    def search_commands(self, search_term: str, limit: int = 5) -> List[str]:
        """Search for commands by name (best), alias, or description."""
        results = []
        search_lower = search_term.lower()
        for cmd_name, cmd_info in self.commands.items():
            if search_lower in cmd_name.lower():
                results.append((cmd_name, 3))
            elif any(search_lower in alias.lower() for alias in cmd_info["aliases"]):
                results.append((cmd_name, 2))
            elif search_lower in cmd_info["description"].lower():
                results.append((cmd_name, 1))
        results.sort(key=lambda x: x[1], reverse=True)
        return [r[0] for r in results[:limit]]

    def get_suggestions(self, user_input: str, limit: int = 3) -> List[Dict]:
        """Get command suggestions based on user input"""
        return [
            {k: self.commands[n][k] for k in ("name", "description", "category")}
            for n in self.search_commands(user_input, limit=limit)
        ]

    def get_all_commands_formatted(self) -> str:
        """All commands formatted for LLM context (cached until the catalogue changes)."""
        if self._formatted is None:
            lines = ["Available commands (use /help for more info):"]
            for category, cmd_names in self.categories.items():
                lines.append(f"\n{category}:")
                for cmd_name in sorted(cmd_names):
                    lines.append(f"- /{cmd_name}: {self.commands[cmd_name]['description']}")
            self._formatted = "\n".join(lines) + "\n"
        return self._formatted


command_db = CommandDatabase()
