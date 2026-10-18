"""Slash command packages. COMMAND_GROUPS is the single registry: each module exposes register(client)."""
import importlib
from typing import Dict, List, Tuple

# group package -> (category shown in /help and the LLM prompt, modules in registration order)
COMMAND_GROUPS: Dict[str, Tuple[str, List[str]]] = {
    "general": ("General", ["status", "checkwake", "bal", "openrouter_check", "cursor", "sleep_mode", "reliability"]),
    "chat": (
        "General",
        [
            "chat",
            "forget",
            "chat_history",
            "adaptive",
            "adaptive_tune",
            "adaptive_tune_batch",
            "adaptive_status",
            "adaptive_tune_channel",
            "fast_reply",
            "conversation",
            "conversation_frequency",
        ],
    ),
    "notes": ("General", ["notes"]),
    "help": ("General", ["help"]),
    "reminder": ("Reminders", ["remind", "reminders", "cancel_reminder"]),
    "cal": ("Calendar", ["cal"]),
    "persona": ("Persona", ["persona_create"]),
    "model": ("Model", ["llm_settings", "pull_model", "imagine"]),
    "download": ("Download", ["download", "download_limit"]),
    "scripts": ("Scripts", ["scripts", "run"]),
    "admin": (
        "Admin",
        ["update", "rollback", "purge", "restart", "kill", "whitelist", "setwake", "sethome", "setstatus", "clone", "profanity", "remover"],
    ),
    "ollama": ("Admin", ["control"]),
    "shitpost": ("Shitpost", ["ignore"]),
    "ha": ("Home Assistant", ["himas", "explain", "listentities", "removeentity", "ha_status", "find_sensor"]),
    "news": ("News", ["news", "news_model", "news_time", "news_source"]),
}


def register_all(client) -> Tuple[List[str], List[Tuple[str, str, str]]]:
    """Register every command module. Returns (errors, [(command, description, category), ...])."""
    errors: List[str] = []
    catalogue: List[Tuple[str, str, str]] = []
    for group, (category, modules) in COMMAND_GROUPS.items():
        for mod_name in modules:
            before = {c.name for c in client.tree.get_commands()}
            try:
                importlib.import_module(f"commands.{group}.{mod_name}").register(client)
            except Exception as e:
                errors.append(f"{group}.{mod_name}: {e}")
                continue
            for cmd in client.tree.get_commands():
                if cmd.name not in before:
                    catalogue.append((cmd.name, cmd.description or "", category))
    return errors, catalogue
