"""Model preferences and Ollama model list"""
import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from utils import home_log
from utils.http import session as http_session
from utils.json_store import load_json, save_json
from utils.ollama import base_url as ollama_base_url
from utils.paths import data_path

MODELS_FILE = data_path("models.json")
DEFAULT_LOCAL_MODEL = "qwen2.5:7b"
_REFRESH_TTL_SECONDS = 30.0


class ModelManager:
    def __init__(self):
        self.available_models: List[str] = []
        self.user_models: Dict[str, Dict] = {}
        self.ollama_online: bool = False
        self._refreshed_at = float("-inf")
        self._refresh_task: Optional[asyncio.Task] = None
        self.load_models()

    def refresh_local_models(self) -> bool:
        """Fetch available models from Ollama (blocking). On failure, available_models becomes []."""
        was_online, prev_count = self.ollama_online, len(self.available_models)
        self._refreshed_at = time.monotonic()
        try:
            response = http_session().get(f"{ollama_base_url()}/api/tags", timeout=5)
            if response.status_code == 200:
                models_data = response.json().get("models", [])
                self.available_models = [m.get("name") for m in models_data if m.get("name")]
                self.ollama_online = True
                if not was_online or len(self.available_models) != prev_count:
                    home_log.log_sync(f"✅ Found {len(self.available_models)} Ollama models")
                return True
        except requests.exceptions.ConnectionError:
            if was_online or prev_count == 0:
                # Console only; the startup embed shows Ollama online/offline instead.
                print(f"⚠️ Cannot connect to Ollama at {ollama_base_url()}")
        except Exception as e:
            home_log.log_sync(f"⚠️ Error fetching models: {e}")
        self.available_models = []
        self.ollama_online = False
        return False

    async def refresh_async(self) -> bool:
        """Refresh the Ollama model list in a worker thread."""
        return await asyncio.to_thread(self.refresh_local_models)

    def _maybe_refresh(self) -> None:
        """Refresh when the list is older than the TTL.

        Inside the event loop this never blocks: a background refresh is scheduled and the cached
        list is used for this call. Outside a loop (threads, startup) it refreshes synchronously.
        """
        if time.monotonic() - self._refreshed_at < _REFRESH_TTL_SECONDS:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.refresh_local_models()
            return
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = loop.create_task(self.refresh_async())

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
            "last_local_model": current.get("last_local_model", DEFAULT_LOCAL_MODEL),
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
            "model": current.get("model", DEFAULT_LOCAL_MODEL),
            "last_local_model": current.get("last_local_model", DEFAULT_LOCAL_MODEL),
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
        default = {
            "provider": "local",
            "model": DEFAULT_LOCAL_MODEL,
            "last_local_model": DEFAULT_LOCAL_MODEL,
            "cloud_history": [],
        }
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
        return {
            "provider": provider,
            "model": model_name,
            "last_local_model": last_local_model,
            "cloud_history": cloud_history,
            "function_models": fm,
        }

    def get_effective_model_for_function(self, user_id, function_key: str) -> Dict[str, str]:
        """Provider + model for a logical LLM function (per-function override or user default)."""
        if str(function_key) == "image_generation":
            ov = self.get_function_model_override(user_id, "image_generation")
            if ov:
                p, m = ov
                if str(p).lower() == "cloud" and str(m or "").strip():
                    return {"provider": "cloud", "model": str(m).strip()}
            return {"provider": "cloud", "model": ""}
        base = self.get_user_model_info(user_id)
        ov = self.get_function_model_override(user_id, function_key)
        if ov:
            p, m = ov
            return {"provider": p, "model": m}
        return {"provider": base["provider"], "model": base["model"]}

    def list_known_image_generation_cloud_models(self) -> List[str]:
        """Unique OpenRouter image model ids saved on any user (for /llm-settings menu)."""
        seen: List[str] = []
        for entry in self.user_models.values():
            if not isinstance(entry, dict):
                continue
            fm = self._normalize_function_models(entry.get("function_models"))
            slot = fm.get("image_generation")
            if not slot:
                continue
            if str(slot.get("provider", "")).lower() != "cloud":
                continue
            m = str(slot.get("model", "") or "").strip()
            if m and m not in seen:
                seen.append(m)
        return seen

    def get_last_local_model(self, user_id, refresh_local: bool = True) -> str:
        info = self.get_user_model_info(user_id)
        preferred = info.get("last_local_model") or DEFAULT_LOCAL_MODEL
        if refresh_local:
            self._maybe_refresh()
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
            self._maybe_refresh()
        return sorted(self.available_models)

    def save_models(self) -> None:
        save_json(MODELS_FILE, {"user_models": self.user_models})

    def load_models(self) -> None:
        data = load_json(MODELS_FILE, {})
        um = data.get("user_models", {}) if isinstance(data, dict) else {}
        self.user_models = um if isinstance(um, dict) else {}


model_manager = ModelManager()
