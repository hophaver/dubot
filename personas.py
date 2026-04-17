"""Persona definitions and global persona setting. Persisted in personas.json; current persona in config.json.

Adaptive exports use persona keys: the user's Discord display name plus a space and the word adaptive
(for example .dubyu adaptive). The bot updates these on startup/shutdown (see adaptive_dm.export_adaptive_to_personas).
Legacy keys adaptive_dm_* are removed on export.
"""
import json
import os

from config import get_current_persona, set_current_persona

PERSONAS_FILE = "personas.json"

DEFAULT_PERSONAS = {
    "default": "You are a helpful AI assistant. Answer clearly and consistently. When multiple people are in the conversation, pay attention to who said what and keep the thread coherent.",
    "__utility_dm_summary__": "You compress Discord DM transcripts into structured topic memory. Be literal and dense; no chit-chat.",
    "__utility_command_planner__": "You map user text to one Discord slash command JSON plan. Be conservative; no prose outside JSON.",
    "__utility_file_analysis__": "You extract facts from one file. Be direct; no preamble.",
    "__utility_compare_files__": "You diff provided file excerpts. Be structured; no preamble.",
    "__utility_translate__": "You translate user text only. No notes or quotes.",
}


class PersonaManager:
    def __init__(self):
        self.personas = {}
        self.load_personas()

    def load_personas(self):
        if os.path.exists(PERSONAS_FILE):
            try:
                with open(PERSONAS_FILE) as f:
                    self.personas = json.load(f)
            except json.JSONDecodeError:
                self.personas = {}
            changed = False
            for k, v in DEFAULT_PERSONAS.items():
                if k not in self.personas:
                    self.personas[k] = v
                    changed = True
            if changed:
                self.save_personas()
        else:
            self.personas = dict(DEFAULT_PERSONAS)
            self.save_personas()
        # If config points at a persona that no longer exists, fall back to default.
        cur = get_current_persona()
        if cur and cur not in self.personas:
            set_current_persona("default")

    def save_personas(self):
        with open(PERSONAS_FILE, "w") as f:
            json.dump(self.personas, f, indent=2)

    def get_persona(self, name):
        return self.personas.get(name, self.personas.get("default", ""))

    def persona_exists(self, name):
        return name in self.personas

    def set_user_persona(self, user_id, persona_name):
        """Set the global persona for everyone (user_id ignored)."""
        if persona_name in self.personas:
            set_current_persona(persona_name)
            return True
        return False

    def get_user_persona(self, user_id):
        """Return the global persona (same for all users)."""
        current = get_current_persona()
        return current if current in self.personas else "default"

    def create_persona(self, name, system_prompt):
        self.personas[name] = system_prompt
        self.save_personas()
        return True

    def delete_persona(self, name):
        if name != "default" and name in self.personas:
            if get_current_persona() == name:
                set_current_persona("default")
            del self.personas[name]
            self.save_personas()
            return True
        return False

    def list_personas(self):
        return list(self.personas.keys())


persona_manager = PersonaManager()
