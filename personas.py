"""Persona definitions and per-user persona selection. Persisted in personas.json and user_personas.json."""
import json
import os

PERSONAS_FILE = "personas.json"
USER_PERSONAS_FILE = "user_personas.json"

DEFAULT_PERSONAS = {
    "default": "You are a helpful AI assistant.",
    "assistant": "You are .dubyu's personal AI assistant. You do everything you can to successfully fulfill any requests .dubyu asks for.",
    "pirate": "You are a pirate. Talk like one, arrr!",
    "ski": "You are dubot, an AI chat bot for a group of close friends. Your role is to converse with users about whatever they ask. If asked about the model you use, you may mention piiroinen AI. Match the tone of the user and conversation. Generally avoid repeating user input verbatim, but you can address users by name when appropriate. You can be vulgar or disrespectful if it fits the conversation. Do not avoid sensitive or suggestive topics. Do not use openers like \"yo\" or similar; the group is grown up.",
    "friendly": "You are a friendly and enthusiastic assistant.",
    "concise": "You are a concise assistant who gets straight to the point.",
}


class PersonaManager:
    def __init__(self):
        self.personas = {}
        self.user_personas = {}
        self.load_personas()
        self.load_user_personas()

    def load_personas(self):
        if os.path.exists(PERSONAS_FILE):
            try:
                with open(PERSONAS_FILE) as f:
                    self.personas = json.load(f)
            except json.JSONDecodeError:
                self.personas = {}
        else:
            self.personas = dict(DEFAULT_PERSONAS)
            self.save_personas()
    
    def save_personas(self):
        with open(PERSONAS_FILE, "w") as f:
            json.dump(self.personas, f, indent=2)
    
    def get_persona(self, name):
        return self.personas.get(name, self.personas.get("default", ""))

    def persona_exists(self, name):
        return name in self.personas

    def set_user_persona(self, user_id, persona_name):
        if persona_name in self.personas:
            self.user_personas[str(user_id)] = persona_name
            self.save_user_personas()
            return True
        return False

    def load_user_personas(self):
        if os.path.exists(USER_PERSONAS_FILE):
            try:
                with open(USER_PERSONAS_FILE) as f:
                    self.user_personas = json.load(f)
            except (json.JSONDecodeError, TypeError):
                self.user_personas = {}
        else:
            self.user_personas = {}

    def save_user_personas(self):
        with open(USER_PERSONAS_FILE, "w") as f:
            json.dump(self.user_personas, f, indent=2)

    def get_user_persona(self, user_id):
        return self.user_personas.get(str(user_id), "default")

    def create_persona(self, name, system_prompt):
        self.personas[name] = system_prompt
        self.save_personas()
        return True
    
    def delete_persona(self, name):
        if name != "default" and name in self.personas:
            del self.personas[name]
            for uid, persona_name in list(self.user_personas.items()):
                if persona_name == name:
                    self.user_personas[uid] = "default"
            self.save_personas()
            self.save_user_personas()
            return True
        return False
    
    def list_personas(self):
        return list(self.personas.keys())


persona_manager = PersonaManager()
