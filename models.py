"""User model preferences and Ollama model list. All models are local (Ollama)."""
import json
import os
import requests
from typing import Dict, List, Optional
from integrations import OLLAMA_URL

MODELS_FILE = "data/models.json"
DEFAULT_FALLBACK = ["qwen2.5:7b", "llama3.2:3b", "llama3.2:1b"]


class ModelManager:
    def __init__(self):
        self.available_models: List[str] = []
        self.user_models: Dict[str, Dict] = {}
        self.load_models()
        self.refresh_local_models()

    def refresh_local_models(self) -> bool:
        try:
            response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=10)
            if response.status_code == 200:
                models_data = response.json().get("models", [])
                self.available_models = [m.get("name") for m in models_data]
                print(f"✅ Found {len(self.available_models)} Ollama models")
                return True
        except requests.exceptions.ConnectionError:
            print(f"⚠️ Cannot connect to Ollama at {OLLAMA_URL}")
        except Exception as e:
            print(f"⚠️ Error fetching models: {e}")
        self.available_models = DEFAULT_FALLBACK.copy()
        return False

    def set_user_model(self, user_id, model_name: str) -> None:
        self.user_models[str(user_id)] = {"provider": "local", "model": model_name}
        self.save_models()

    def get_user_model_info(self, user_id):
        default = {"provider": "local", "model": "qwen2.5:7b"}
        return self.user_models.get(str(user_id), default)

    def list_all_models(self, refresh_local: bool = False) -> List[str]:
        if refresh_local:
            self.refresh_local_models()
        return sorted(self.available_models)

    def check_model_availability(self, model_name: str) -> tuple[bool, str]:
        if model_name in self.available_models:
            return True, "Model found"
        self.refresh_local_models()
        if model_name in self.available_models:
            return True, "Model found after refresh"
        return False, f"Model '{model_name}' not found. Use /pull-model to download."

    def suggest_alternative_models(self, failed_model: str) -> List[str]:
        suggestions = []
        base = failed_model.split(":")[0] if ":" in failed_model else failed_model
        for m in self.available_models:
            if m == failed_model:
                continue
            if base in m or (("llama" in failed_model.lower() and "llama" in m.lower()) or ("qwen" in failed_model.lower() and "qwen" in m.lower())):
                suggestions.append(m)
        return list(dict.fromkeys(suggestions))[:5]

    def get_ha_context(self, user_id: int) -> str:
        try:
            with open("ha_entities_cache.json") as f:
                entities = json.load(f)
            lines = []
            for entity_id, data in list(entities.items())[:20]:
                name = data.get("attributes", {}).get("friendly_name", "")
                if name:
                    lines.append(f"{name} -> {entity_id}")
            if lines:
                return "Available entities:\n" + "\n".join(lines)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return "No entity cache. Use /listentities to update."

    def suggest_ha_entities(self, search_term: str) -> List[str]:
        try:
            with open("ha_entities_cache.json") as f:
                entities = json.load(f)
            search = search_term.lower()
            out = []
            for entity_id, data in entities.items():
                name = data.get("attributes", {}).get("friendly_name", "")
                if search in entity_id.lower() or search in name.lower():
                    out.append(f"{name} ({entity_id})")
            return out[:10]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save_models(self) -> None:
        os.makedirs(os.path.dirname(MODELS_FILE), exist_ok=True)
        with open(MODELS_FILE, "w") as f:
            json.dump({"user_models": self.user_models}, f, indent=2)

    def load_models(self) -> None:
        if os.path.exists(MODELS_FILE):
            try:
                with open(MODELS_FILE) as f:
                    self.user_models = json.load(f).get("user_models", {})
            except json.JSONDecodeError:
                self.user_models = {}
        else:
            self.save_models()


model_manager = ModelManager()
