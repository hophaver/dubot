"""Model preferences and Ollama model list"""
import json
import os
import requests
from typing import Any, Dict, List, Optional, Tuple
from integrations import OLLAMA_URL
from utils import home_log

MODELS_FILE = "data/models.json"
DEFAULT_FALLBACK = ["qwen2.5:7b", "llama3.2:3b", "llama3.2:1b"]


class ModelManager:
    def __init__(self):
        self.available_models: List[str] = []
        self.user_models: Dict[str, Dict] = {}
        self.load_models()
        self.refresh_local_models()

    def refresh_local_models(self) -> bool:
        """Fetch available models from Ollama. On failure, set available_models to [] (no fake list)."""
        try:
            response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=10)
            if response.status_code == 200:
                models_data = response.json().get("models", [])
                self.available_models = [m.get("name") for m in models_data if m.get("name")]
                home_log.log_sync(f"✅ Found {len(self.available_models)} Ollama models")
                return True
        except requests.exceptions.ConnectionError:
            home_log.log_sync(f"⚠️ Cannot connect to Ollama at {OLLAMA_URL}")
        except Exception as e:
            home_log.log_sync(f"⚠️ Error fetching models: {e}")
        self.available_models = []
        return False

    def set_user_model(self, user_id, model_name: str, provider: str = "local") -> None:
        provider = (provider or "local").strip().lower()
        if provider not in {"local", "cloud"}:
            provider = "local"
        key = str(user_id)
        current = self.user_models.get(key, {}) if isinstance(self.user_models.get(key), dict) else {}
        cloud_history = current.get("cloud_history", [])
        if not isinstance(cloud_history, list):
            cloud_history = []
        cloud_history = [str(m).strip() for m in cloud_history if str(m).strip()]
        fm = self._normalize_function_models(current.get("function_models"))
        entry = {
            "provider": provider,
            "model": model_name,
            "last_local_model": current.get("last_local_model", "qwen2.5:7b"),
            "cloud_history": cloud_history,
            "function_models": fm,
        }
        if provider == "local":
            entry["last_local_model"] = model_name
        else:
            history = [m for m in cloud_history if m != model_name]
            history.insert(0, model_name)
            entry["cloud_history"] = history[:25]
        self.user_models[key] = entry
        self.save_models()

    def _normalize_function_models(self, raw: Any) -> Dict[str, Dict[str, str]]:
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, Dict[str, str]] = {}
        for k, v in raw.items():
            if not isinstance(v, dict):
                continue
            prov = str(v.get("provider", "local")).strip().lower()
            if prov not in {"local", "cloud"}:
                prov = "local"
            m = str(v.get("model", "") or "").strip()
            if not m:
                continue
            out[str(k)] = {"provider": prov, "model": m}
        return out

    def get_function_model_override(self, user_id, function_key: str) -> Optional[Tuple[str, str]]:
        """Return (provider, model) if this function has an override, else None."""
        key = str(user_id)
        entry = self.user_models.get(key)
        if not isinstance(entry, dict):
            return None
        fm = self._normalize_function_models(entry.get("function_models"))
        slot = fm.get(str(function_key))
        if not slot:
            return None
        return slot["provider"], slot["model"]

    def set_function_model(self, user_id, function_key: str, model_name: str, provider: str = "local") -> None:
        provider = (provider or "local").strip().lower()
        if provider not in {"local", "cloud"}:
            provider = "local"
        key = str(user_id)
        current = self.user_models.get(key, {}) if isinstance(self.user_models.get(key), dict) else {}
        cloud_history = current.get("cloud_history", [])
        if not isinstance(cloud_history, list):
            cloud_history = []
        cloud_history = [str(m).strip() for m in cloud_history if str(m).strip()]
        fm = self._normalize_function_models(current.get("function_models"))
        fm[str(function_key)] = {"provider": provider, "model": model_name}
        entry = {
            "provider": current.get("provider", "local"),
            "model": current.get("model", "qwen2.5:7b"),
            "last_local_model": current.get("last_local_model", "qwen2.5:7b"),
            "cloud_history": cloud_history,
            "function_models": fm,
        }
        if provider == "local":
            entry["last_local_model"] = model_name
        else:
            history = [m for m in cloud_history if m != model_name]
            history.insert(0, model_name)
            entry["cloud_history"] = history[:25]
        self.user_models[key] = entry
        self.save_models()

    def clear_function_model(self, user_id, function_key: str) -> None:
        key = str(user_id)
        entry = self.user_models.get(key)
        if not isinstance(entry, dict):
            return
        fm = self._normalize_function_models(entry.get("function_models"))
        fk = str(function_key)
        if fk in fm:
            del fm[fk]
        entry["function_models"] = fm
        self.user_models[key] = entry
        self.save_models()

    def get_user_model_info(self, user_id):
        default = {"provider": "local", "model": "qwen2.5:7b", "last_local_model": "qwen2.5:7b", "cloud_history": []}
        model_info = self.user_models.get(str(user_id), default)
        if not isinstance(model_info, dict):
            return default
        provider = str(model_info.get("provider", "local")).strip().lower()
        if provider not in {"local", "cloud"}:
            provider = "local"
        model_name = str(model_info.get("model", default["model"])).strip() or default["model"]
        last_local_model = str(model_info.get("last_local_model", "")).strip()
        cloud_history = model_info.get("cloud_history", [])
        if not isinstance(cloud_history, list):
            cloud_history = []
        cloud_history = [str(m).strip() for m in cloud_history if str(m).strip()]
        if provider == "cloud" and model_name and model_name not in cloud_history:
            cloud_history.insert(0, model_name)
        cloud_history = cloud_history[:25]
        if not last_local_model:
            last_local_model = model_name if provider == "local" else default["last_local_model"]
        if provider == "local":
            last_local_model = model_name
        fm = self._normalize_function_models(model_info.get("function_models"))
        prev_fm = self._normalize_function_models(model_info.get("function_models"))
        if (
            provider != model_info.get("provider")
            or model_name != model_info.get("model")
            or last_local_model != model_info.get("last_local_model")
            or cloud_history != model_info.get("cloud_history")
            or fm != prev_fm
        ):
            self.user_models[str(user_id)] = {
                "provider": provider,
                "model": model_name,
                "last_local_model": last_local_model,
                "cloud_history": cloud_history,
                "function_models": fm,
            }
            self.save_models()
        return {
            "provider": provider,
            "model": model_name,
            "last_local_model": last_local_model,
            "cloud_history": cloud_history,
            "function_models": fm,
        }

    def get_effective_model_for_function(self, user_id, function_key: str) -> Dict[str, str]:
        """Provider + model for a logical LLM function (per-function override or user default)."""
        base = self.get_user_model_info(user_id)
        ov = self.get_function_model_override(user_id, function_key)
        if ov:
            p, m = ov
            return {"provider": p, "model": m}
        return {"provider": base["provider"], "model": base["model"]}

    def get_recent_cloud_models(self, user_id: int) -> List[str]:
        info = self.get_user_model_info(user_id)
        history = info.get("cloud_history", [])
        if not isinstance(history, list):
            return []
        cleaned = [m for m in history if isinstance(m, str) and m.strip()]
        return list(dict.fromkeys(cleaned))

    def get_last_local_model(self, user_id, refresh_local: bool = True) -> str:
        info = self.get_user_model_info(user_id)
        preferred = info.get("last_local_model") or "qwen2.5:7b"
        if refresh_local:
            self.refresh_local_models()
        if preferred in self.available_models:
            return preferred
        if self.available_models:
            fallback = sorted(self.available_models)[0]
            if fallback != preferred:
                key = str(user_id)
                entry = self.user_models.get(key, {})
                if isinstance(entry, dict):
                    entry["last_local_model"] = fallback
                    self.user_models[key] = entry
                    self.save_models()
            return fallback
        return preferred

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
