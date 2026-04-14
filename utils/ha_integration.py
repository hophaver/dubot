import re
import json
import asyncio
import aiohttp
from typing import Dict, List, Optional, Tuple, Any
from integrations import (
    HA_URL,
    HA_ACCESS_TOKEN,
    HIMAS_ASSIST_ENABLED,
    HIMAS_ASSIST_LANGUAGE,
    HIMAS_ASSIST_AGENT_ID,
    HIMAS_PARSE_PROVIDER,
    HIMAS_PARSE_MODEL,
    OLLAMA_URL,
    OPENROUTER_API_KEY,
)
import sys
import os

from utils import home_log

# Add the project root to the path so we can import from utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Optional allowlist: only these entity_ids are visible to the bot (data/ha_entities_allowlist.json).
# If missing or empty, all entities from the HA token are used.
HA_ENTITIES_ALLOWLIST_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ha_entities_allowlist.json")

# Home Assistant service constants
HA_SERVICES = {
    "light": ["turn_on", "turn_off", "toggle", "brightness", "color", "effect"],
    "switch": ["turn_on", "turn_off", "toggle"],
    "fan": ["turn_on", "turn_off", "toggle", "speed"],
    "climate": ["set_temperature", "set_hvac_mode", "set_fan_mode", "set_preset_mode"],
    "cover": ["open_cover", "close_cover", "stop_cover", "set_cover_position"],
    "media_player": ["play_media", "media_play", "media_pause", "volume_set", "select_source"],
    "sensor": ["get_state"],
    "binary_sensor": ["get_state"]
}

# Color mapping for lights
COLOR_MAP = {
    "red": [255, 0, 0],
    "green": [0, 255, 0],
    "blue": [0, 0, 255],
    "white": [255, 255, 255],
    "warm white": [255, 223, 186],
    "yellow": [255, 255, 0],
    "orange": [255, 165, 0],
    "purple": [128, 0, 128],
    "pink": [255, 192, 203],
    "cyan": [0, 255, 255],
    "magenta": [255, 0, 255],
    "cool white": [230, 230, 255]
}

def _extract_conversation_assist_result(payload: Any) -> Tuple[str, str]:
    """Parse HA /api/conversation/process JSON -> (speech_text, response_type)."""
    if not isinstance(payload, dict):
        return "", ""
    inner = payload.get("response")
    if not isinstance(inner, dict):
        return "", ""
    rt = str(inner.get("response_type") or "").strip()
    speech = inner.get("speech") or {}
    text = ""
    if isinstance(speech, dict):
        plain = speech.get("plain")
        if isinstance(plain, dict):
            text = str(plain.get("speech") or plain.get("text") or "").strip()
        elif isinstance(plain, str):
            text = plain.strip()
    return text, rt


class HomeAssistantManager:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {HA_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        self.entities_cache = {}
        self.last_update = 0
        self.session = None
        self._himas_llm_label = ""
    
    async def get_session(self):
        """Get or create aiohttp session"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None

    def _resolve_himas_parse_model(self, user_id: int) -> Tuple[str, str]:
        """Return (backend, model) for LLM JSON parsing fallback: ollama or openrouter."""
        from models import model_manager

        p = (HIMAS_PARSE_PROVIDER or "auto").strip().lower()
        if p not in ("auto", "ollama", "openrouter", "local"):
            p = "auto"
        if p == "local":
            p = "ollama"
        fixed = (HIMAS_PARSE_MODEL or "").strip()

        if p == "openrouter":
            if fixed:
                return "openrouter", fixed
            info = model_manager.get_user_model_info(user_id)
            cm = str(info.get("model") or "").strip() if info.get("provider") == "cloud" else ""
            if cm:
                return "openrouter", cm
            return "ollama", model_manager.get_last_local_model(user_id, refresh_local=True)

        if p == "ollama":
            if fixed:
                return "ollama", fixed
            return "ollama", model_manager.get_last_local_model(user_id, refresh_local=True)

        # auto: slash often means OpenRouter (openai/gpt-4o-mini), but Ollama can use hf.co/... IDs.
        if fixed:
            if "/" in fixed and not fixed.lower().startswith("hf.co/"):
                return "openrouter", fixed
            return "ollama", fixed
        info = model_manager.get_user_model_info(user_id)
        if info.get("provider") == "cloud" and str(info.get("model") or "").strip():
            return "openrouter", str(info.get("model")).strip()
        return "ollama", model_manager.get_last_local_model(user_id, refresh_local=True)

    async def _process_with_assist(self, text: str) -> Optional[Dict[str, str]]:
        """Run Home Assistant Assist via REST. HA executes intents; returns user-facing reply or None to fall back."""
        if not HIMAS_ASSIST_ENABLED or not (text or "").strip():
            return None
        body: Dict[str, Any] = {"text": text.strip(), "language": HIMAS_ASSIST_LANGUAGE or "en"}
        if HIMAS_ASSIST_AGENT_ID:
            body["agent_id"] = HIMAS_ASSIST_AGENT_ID
        try:
            session = await self.get_session()
            async with session.post(
                f"{HA_URL.rstrip('/')}/api/conversation/process",
                headers=self.headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as response:
                raw_text = await response.text()
                if response.status != 200:
                    home_log.log_sync(f"Assist conversation HTTP {response.status}: {raw_text[:300]}")
                    return None
                try:
                    data = json.loads(raw_text) if raw_text else {}
                except json.JSONDecodeError:
                    return None
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
            home_log.log_sync(f"Assist request failed: {e}")
            return None
        except Exception as e:
            home_log.log_sync(f"Assist error: {e}")
            return None

        if not isinstance(data, dict) or "response" not in data:
            return None
        speech, rt = _extract_conversation_assist_result(data)
        if rt == "error":
            return None
        if not speech:
            speech = "OK."
        return {"message": speech[:4096]}

    async def _openrouter_parse_json(self, system_prompt: str, user_tail: str, model_name: str) -> str:
        if not OPENROUTER_API_KEY:
            return ""
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_tail},
            ],
            "temperature": 0.1,
            "max_tokens": 400,
        }
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            session = await self.get_session()
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    err = body.get("error", {}) if isinstance(body, dict) else {}
                    msg = err.get("message", str(body)[:200]) if isinstance(err, dict) else str(body)[:200]
                    home_log.log_sync(f"OpenRouter himas parse HTTP {response.status}: {msg}")
                    return ""
                choices = body.get("choices") or []
                if not choices:
                    return ""
                message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                content = message.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ]
                    content = "".join(text_parts)
                return str(content).strip()
        except Exception as e:
            home_log.log_sync(f"OpenRouter himas parse error: {e}")
            return ""
    
    def _get_entity_allowlist(self) -> Optional[List[str]]:
        """Return list of allowed entity_ids from data/ha_entities_allowlist.json, or None if not used."""
        try:
            if not os.path.isfile(HA_ENTITIES_ALLOWLIST_FILE):
                return None
            with open(HA_ENTITIES_ALLOWLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            ids = data if isinstance(data, list) else data.get("entity_ids", data.get("allowlist", []))
            if not ids:
                return None
            return [str(e).strip() for e in ids if e]
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    async def get_all_entities(self, force_refresh: bool = False) -> Dict:
        """Get all entities from Home Assistant with caching. If ha_entities_allowlist.json exists, only those entities are returned."""
        import time

        # Return cached entities if recent and not forced
        current_time = time.time()
        if not force_refresh and self.entities_cache and (current_time - self.last_update) < 300:  # 5 minutes
            return self.entities_cache

        try:
            session = await self.get_session()
            async with session.get(
                f"{HA_URL}/api/states",
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    entities = await response.json()
                    self.entities_cache = {entity["entity_id"]: entity for entity in entities}
                    allowlist = self._get_entity_allowlist()
                    if allowlist:
                        self.entities_cache = {eid: self.entities_cache[eid] for eid in allowlist if eid in self.entities_cache}
                    self.last_update = current_time
                    return self.entities_cache
                else:
                    home_log.log_sync(f"Error fetching HA entities: {response.status}")
                    return {}
        except Exception as e:
            home_log.log_sync(f"Error fetching HA entities: {e}")
            return {}
    
    async def get_entity_state(self, entity_id: str) -> Optional[Dict]:
        """Get current state of an entity"""
        try:
            session = await self.get_session()
            async with session.get(
                f"{HA_URL}/api/states/{entity_id}",
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    home_log.log_sync(f"Error getting entity state {entity_id}: {response.status}")
                    return None
        except Exception as e:
            home_log.log_sync(f"Error getting entity state {entity_id}: {e}")
            return None
    
    async def call_service(self, domain: str, service: str, data: Dict) -> Tuple[bool, str]:
        """Call a Home Assistant service"""
        try:
            session = await self.get_session()
            url = f"{HA_URL}/api/services/{domain}/{service}"
            
            async with session.post(
                url,
                headers=self.headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    return True, "Command executed successfully"
                else:
                    error_text = await response.text()
                    return False, f"Error {response.status}: {error_text[:100]}"
                    
        except aiohttp.ClientConnectionError:
            return False, "Cannot connect to Home Assistant"
        except asyncio.TimeoutError:
            return False, "Request timed out"
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def _split_multi_command(self, user_command: str) -> List[str]:
        """Split command by 'and', ',', 'then' for multiple entities/actions."""
        text = user_command.strip()
        text = re.sub(r"\s+then\s+", " and ", text, flags=re.I)
        text = re.sub(r"\s*,\s*", " and ", text)
        parts = [p.strip() for p in re.split(r"\s+and\s+", text, flags=re.I) if p.strip()]
        return parts if parts else [user_command.strip()]

    def parse_basic_command(self, user_command: str) -> Optional[Dict]:
        """Parse basic commands without LLM for common patterns"""
        command_lower = user_command.lower().strip()

        # "X off" / "X on" shorthand first
        off_on = re.match(r"^(.+?)\s+(off|on)\s*$", command_lower)
        if off_on:
            entity = off_on.group(1).strip()
            action = "turn_off" if off_on.group(2) == "off" else "turn_on"
            return {
                "type": "control",
                "action": action,
                "entity_name": entity,
                "parameters": {}
            }

        # "X to 50%" or "X at 70%" (so "ceiling to 50%" -> entity "ceiling")
        to_at = re.match(r"^(.+?)\s+(?:to|at)\s+(\d{1,3})\s*%$", command_lower)
        if to_at:
            entity = to_at.group(1).strip()
            brightness = max(0, min(100, int(to_at.group(2))))
            return {
                "type": "control",
                "action": "set_brightness",
                "entity_name": entity,
                "parameters": {"brightness": brightness}
            }
        # "ceiling blue 50%" or "ceiling lamp red 80%" -> color + brightness (before generic "X 50%")
        color_pct = re.match(
            r"^(?:set\s+)?(.+?)\s+(red|green|blue|white|warm white|yellow|orange|purple|pink|cyan|magenta|cool white)\s+(\d{1,3})\s*%$",
            command_lower
        )
        if color_pct:
            entity = color_pct.group(1).strip()
            color = color_pct.group(2).strip().lower()
            brightness = max(0, min(100, int(color_pct.group(3))))
            if color in COLOR_MAP:
                return {
                    "type": "control",
                    "action": "set_color_and_brightness",
                    "entity_name": entity,
                    "parameters": {"color": color, "brightness": brightness}
                }
        # Shorthand: "ceiling 50%" or "living room 80%" -> set brightness
        shorthand = re.match(r"^(.+?)\s+(\d{1,3})\s*%$", command_lower)
        if shorthand:
            entity = shorthand.group(1).strip()
            brightness = max(0, min(100, int(shorthand.group(2))))
            return {
                "type": "control",
                "action": "set_brightness",
                "entity_name": entity,
                "parameters": {"brightness": brightness}
            }

        # Basic patterns
        patterns = [
            (r"(?:turn on|switch on|enable) (.+?)(?: please)?$", "turn_on"),
            (r"(?:turn off|switch off|disable) (.+?)(?: please)?$", "turn_off"),
            (r"toggle (.+?)(?: please)?$", "toggle"),
            (r"set (.+?) (?:to |at |)(\d{1,3})%", "brightness"),
            (r"make (.+?) (\d{1,3})%", "brightness"),
            (r"dim (.+?) (?:to |)(\d{1,3})%", "brightness"),
            (r"brightness of (.+?) (?:to |)(\d{1,3})%", "brightness"),
            (r"change (.+?) (?:to |)(\d{1,3})%", "brightness"),
            (r"(?:what(?:'s| is) the (?:temperature|status|state) of (.+?)\??)", "query"),
            (r"is (.+?) (?:on|off)\??", "query_binary"),
        ]
        
        for pattern, action in patterns:
            match = re.search(pattern, command_lower)
            if match:
                if action == "brightness":
                    entity = match.group(1).strip()
                    brightness = int(match.group(2))
                    # Clamp brightness to 0-100
                    brightness = max(0, min(100, brightness))
                    return {
                        "type": "control",
                        "action": "set_brightness",
                        "entity_name": entity,
                        "parameters": {"brightness": brightness}
                    }
                elif action in ["query", "query_binary"]:
                    entity = match.group(1).strip()
                    return {
                        "type": "query",
                        "entity_name": entity,
                        "parameters": {}
                    }
                else:
                    entity = match.group(1).strip()
                    return {
                        "type": "control",
                        "action": action,
                        "entity_name": entity,
                        "parameters": {}
                    }
        
        # "set X to COLOR" or "X COLOR" (color only)
        color_match = re.search(r"(?:set|change) (.+?) to (\w+)(?: color)?", command_lower)
        if color_match:
            entity = color_match.group(1).strip()
            color = color_match.group(2).strip()
            if color in COLOR_MAP:
                return {
                    "type": "control",
                    "action": "set_color",
                    "entity_name": entity,
                    "parameters": {"color": color}
                }
        
        return None

    async def _parse_with_llm(self, user_command: str, user_id: int, error_context: Optional[str] = None) -> Dict:
        """Parse a single command with LLM; optional error_context when a previous attempt failed."""
        backend, model_name = self._resolve_himas_parse_model(user_id)
        self._himas_llm_label = f"OpenRouter `{model_name}`" if backend == "openrouter" else f"Ollama `{model_name}`"

        try:
            entities = await self.get_all_entities()
            entity_context = []
            for entity_id, entity in list(entities.items())[:80]:
                friendly_name = entity.get('attributes', {}).get('friendly_name', '')
                if friendly_name:
                    entity_context.append(f"{friendly_name} -> {entity_id}")

            extra = ""
            if error_context:
                extra = f"\n\nPrevious attempt failed: {error_context}. Suggest a valid command using the entity list above (use friendly_name in entity_name)."

            system_prompt = f"""You are a Home Assistant control system. Convert natural language into a single JSON command.

Available entities (friendly_name -> entity_id):
{chr(10).join(entity_context)}

Output exactly one JSON object, no other text:
- Control: {{"type": "control", "action": "turn_on|turn_off|toggle|set_brightness|set_color|set_color_and_brightness", "entity_name": "friendly name from list", "parameters": {{}}}}
- Query: {{"type": "query", "entity_name": "friendly name", "parameters": {{}}}}

Actions: turn_on, turn_off, toggle, set_brightness (parameters.brightness 0-100), set_color (parameters.color: red/green/blue/white/...), set_color_and_brightness (parameters.color + parameters.brightness).
Use entity_name from the list above. Only valid JSON.{extra}

User command: "{user_command}"
"""
            user_tail = f'Convert to HA command: {user_command}'

            response_text = ""
            if backend == "openrouter":
                response_text = await self._openrouter_parse_json(system_prompt, user_tail, model_name)
            else:
                base_url = (OLLAMA_URL or "http://localhost:11434").rstrip("/")
                prompt = f"### System:\n{system_prompt}\n\n### User:\n{user_tail}\n\n### Assistant:\n"
                session = await self.get_session()
                async with session.post(
                    f"{base_url}/api/generate",
                    json={
                        "model": model_name,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 300}
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        response_text = result.get("response", "").strip()

            if response_text:
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    try:
                        return json.loads(json_match.group())
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            home_log.log_sync(f"LLM parse error: {e}")
        return {"type": "error", "message": f"Could not parse: {user_command}"}

    async def parse_natural_language(self, user_command: str, user_id: int) -> Dict:
        """Parse natural language command using basic patterns first, then LLM."""
        basic_parse = self.parse_basic_command(user_command)
        if basic_parse:
            return basic_parse
        return await self._parse_with_llm(user_command, user_id)
    
    def find_entity_by_name(self, entity_name: str, entities_cache: Dict) -> Optional[str]:
        """Find entity ID by friendly name or partial match"""
        entity_name_lower = entity_name.lower()
        
        # Exact match in cache
        for entity_id, entity in entities_cache.items():
            friendly_name = entity.get('attributes', {}).get('friendly_name', '').lower()
            if entity_name_lower == friendly_name:
                return entity_id
        
        # Partial match in friendly name
        for entity_id, entity in entities_cache.items():
            friendly_name = entity.get('attributes', {}).get('friendly_name', '').lower()
            if entity_name_lower in friendly_name or entity_name_lower.replace(' ', '_') in entity_id:
                return entity_id
        
        # Try to match by domain
        domain_match = re.match(r'^(light|switch|sensor|binary_sensor|climate|fan|cover|media_player)\s+(.+)$', entity_name_lower)
        if domain_match:
            domain = domain_match.group(1)
            name_part = domain_match.group(2).replace(' ', '_')
            for entity_id in entities_cache.keys():
                if entity_id.startswith(f"{domain}.") and name_part in entity_id:
                    return entity_id
        
        return None
    
    async def execute_command(self, command_data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        """Execute parsed Home Assistant command"""
        command_type = command_data.get("type")
        entity_name = command_data.get("entity_name")
        
        if not entity_name:
            return False, "No entity specified", None
        
        # Get all entities
        entities = await self.get_all_entities()
        
        # Find entity by name
        entity_id = self.find_entity_by_name(entity_name, entities)
        
        if not entity_id:
            # Try custom mappings from data/ha_mappings.json (from /explain)
            try:
                mapping_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ha_mappings.json")
                if os.path.exists(mapping_file):
                    with open(mapping_file, 'r') as f:
                        mappings = json.load(f)
                    entity_id = mappings.get(entity_name.lower()) or mappings.get(entity_name.strip().lower())
            except Exception:
                pass
            
        if not entity_id:
            return False, f"Could not find entity: \"{entity_name}\". Use /explain to add a mapping, e.g. /explain \"{entity_name}\" light.your_entity_id", None
        
        if command_type == "control":
            action = command_data.get("action")
            parameters = command_data.get("parameters", {})
            
            # Extract domain from entity_id
            domain = entity_id.split('.')[0] if '.' in entity_id else None
            
            if not domain:
                return False, f"Invalid entity ID: {entity_id}", None
            
            # Map action to HA service
            service_map = {
                "turn_on": "turn_on",
                "turn_off": "turn_off",
                "toggle": "toggle",
                "set_brightness": "turn_on",
                "set_color": "turn_on",
                "set_color_and_brightness": "turn_on",
            }
            
            service = service_map.get(action)
            if not service:
                return False, f"Unknown action: {action}", None
            
            data = {"entity_id": entity_id}
            
            if action == "set_brightness":
                brightness = parameters.get("brightness", 100)
                data["brightness_pct"] = brightness
                data["transition"] = 0.3
            elif action == "set_color_and_brightness":
                brightness = parameters.get("brightness", 100)
                color_name = parameters.get("color", "white").lower()
                data["brightness_pct"] = brightness
                data["transition"] = 0.3
                if color_name in COLOR_MAP:
                    data["rgb_color"] = COLOR_MAP[color_name]
                else:
                    data["rgb_color"] = [255, 255, 255]
            elif action == "set_color":
                color_name = parameters.get("color", "white").lower()
                if color_name in COLOR_MAP:
                    data["rgb_color"] = COLOR_MAP[color_name]
                else:
                    if re.match(r'^\d+,\s*\d+,\s*\d+$', color_name):
                        rgb = [int(x.strip()) for x in color_name.split(',')]
                        data["rgb_color"] = rgb
                    else:
                        data["rgb_color"] = [255, 255, 255]
            elif action in ["turn_on", "turn_off"]:
                pass
            
            success, message = await self.call_service(domain, service, data)
            
            if success and action == "set_brightness":
                message = f"Set brightness to {brightness}%"
            elif success and action == "set_color_and_brightness":
                message = f"Set to {color_name} at {brightness}%"
            
            return success, message, {"entity_id": entity_id, "action": action}
            
        elif command_type == "query":
            entity_state = await self.get_entity_state(entity_id)
            if not entity_state:
                return False, f"Entity {entity_id} not found", None
            
            state = entity_state.get("state")
            attributes = entity_state.get("attributes", {})
            friendly_name = attributes.get("friendly_name", entity_id)
            
            # Format response based on entity type
            if entity_id.startswith("sensor."):
                unit = attributes.get("unit_of_measurement", "")
                return True, f"{friendly_name}: {state}{unit}", entity_state
            elif entity_id.startswith("binary_sensor."):
                return True, f"{friendly_name} is {state}", entity_state
            else:
                # For lights, show brightness if available
                if entity_id.startswith("light.") and "brightness" in attributes:
                    brightness = attributes["brightness"]
                    brightness_pct = round((brightness / 255) * 100)
                    return True, f"{friendly_name}: {state} (brightness: {brightness_pct}%)", entity_state
                return True, f"{friendly_name}: {state}", entity_state
            
        else:
            return False, f"Unknown command type: {command_type}", None
    
    async def format_response(self, success: bool, message: str, original_command: str, 
                            command_data: Optional[Dict] = None, entity_data: Optional[Dict] = None) -> str:
        """Format a user-friendly response"""
        if success:
            if command_data and command_data.get("type") == "query":
                # For queries, just return the value
                return f"📊 {message}"
            else:
                # For control commands, provide confirmation
                entity_name = "device"
                if entity_data and 'attributes' in entity_data:
                    entity_name = entity_data['attributes'].get('friendly_name', 'device')
                elif command_data and 'entity_name' in command_data:
                    entity_name = command_data['entity_name']
                
                action = command_data.get("action", "executed command")
                action_text = action.replace('_', ' ')
                
                if action == "set_brightness":
                    brightness = command_data.get("parameters", {}).get("brightness", 100)
                    return f"✅ {entity_name.title()} brightness set to {brightness}%"
                if action == "set_color_and_brightness":
                    brightness = command_data.get("parameters", {}).get("brightness", 100)
                    color = command_data.get("parameters", {}).get("color", "white")
                    return f"✅ {entity_name.title()} set to {color} at {brightness}%"
                
                return f"✅ {entity_name.title()} {action_text}.\n{message}"
        else:
            # Provide helpful suggestions
            suggestions = ""
            if "Could not find entity" in message:
                suggestions = "\n\nTry:\n- Using /listentities to see available devices\n- Using /explain to add device mappings"
            
            return f"❌ {message}{suggestions}"
    
    async def process_natural_command(self, user_command: str, user_id: int) -> Tuple[str, str]:
        """Process natural language. Tries Assist first; then split/parse/execute with optional LLM fallback."""
        self._himas_llm_label = ""
        clean_command = re.sub(r'\[.*?says:\]\s*', '', user_command).strip()

        if HIMAS_ASSIST_ENABLED and clean_command:
            ar = await self._process_with_assist(clean_command)
            if ar and ar.get("message"):
                footer = "Home Assistant Assist"
                if HIMAS_ASSIST_AGENT_ID:
                    footer += f" · `{HIMAS_ASSIST_AGENT_ID}`"
                return ar["message"], footer

        parts = self._split_multi_command(clean_command)
        results = []
        assist_fallback_prefix = (
            "Assist did not handle · " if (HIMAS_ASSIST_ENABLED and clean_command) else ""
        )

        for part in parts:
            command_data = await self.parse_natural_language(part, user_id)
            if command_data.get("type") == "error":
                command_data = await self._parse_with_llm(part, user_id)
            if command_data.get("type") == "error":
                results.append(f"❌ {command_data.get('message', part)}")
                continue
            success, message, extra_data = await self.execute_command(command_data)
            executed_data = command_data
            if not success:
                retry_data = await self._parse_with_llm(part, user_id, error_context=message)
                if retry_data.get("type") != "error":
                    success, message, extra_data = await self.execute_command(retry_data)
                    executed_data = retry_data
            resp = await self.format_response(success, message, part, executed_data, extra_data)
            results.append(resp)

        out = "\n\n".join(results)
        if self._himas_llm_label:
            footer = f"{assist_fallback_prefix}{self._himas_llm_label}"
        else:
            footer = f"{assist_fallback_prefix}Pattern match" if assist_fallback_prefix else "Pattern match"
        return out, footer

ha_manager = HomeAssistantManager()


async def ask_home_assistant_meta(user_command: str, user_id: int = None) -> Tuple[str, str]:
    """Returns (reply_text, footer_line) for /himas attribution (Assist vs LLM)."""
    if user_id is None:
        user_id = 0
    return await ha_manager.process_natural_command(user_command, user_id)


async def ask_home_assistant(user_command: str, user_id: int = None) -> str:
    """Main function for Home Assistant commands"""
    if user_id is None:
        user_id = 0
    text, _ = await ask_home_assistant_meta(user_command, user_id)
    return text
