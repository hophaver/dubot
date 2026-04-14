import re
import json
import os
import asyncio
import base64
import mimetypes
import time
import requests
from typing import Dict, List, Optional, Tuple, Any
import integrations
from integrations import OLLAMA_URL, OPENROUTER_CHAT_API_KEY, update_system_time_date, get_location_by_ip
from conversations import conversation_manager
from personas import persona_manager
from models import model_manager
from jarvis import jarvis_manager
from utils import home_log
from utils import reliability_telemetry

def _get_fallback_chain():
    from utils.model_fallback import get_fallback_chain
    return get_fallback_chain()

_system_prompts_cache = None

def _get_system_prompts():
    global _system_prompts_cache
    if _system_prompts_cache is None:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "system_prompts.json")
        try:
            with open(path) as f:
                _system_prompts_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _system_prompts_cache = {}
    return _system_prompts_cache

def get_enhanced_prompt(key: str, **kwargs) -> str:
    prompts = _get_system_prompts()
    tpl = prompts.get(key, "")
    for k, v in kwargs.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl

# File support configuration
SUPPORTED_FILE_TYPES = {
    'image': ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'],
    'text': ['.txt', '.json', '.csv', '.xml', '.yaml', '.yml', '.md', '.log'],
    'code': ['.py', '.js', '.html', '.css', '.java', '.cpp', '.c', '.go', '.rs', '.php', '.sh'],
    'document': ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']
}
SUPPORTED_EXTENSION_TO_TYPE = {
    ext: file_type
    for file_type, extensions in SUPPORTED_FILE_TYPES.items()
    for ext in extensions
}

# Command database for LLM awareness
class CommandDatabase:
    def __init__(self):
        self.commands = {}
        self.categories = {}
    
    def add_command(self, name: str, description: str, category: str = "General"):
        """Add a command to the database"""
        self.commands[name] = {
            "name": name,
            "description": description,
            "category": category,
            "aliases": []
        }
        
        if category not in self.categories:
            self.categories[category] = []
        self.categories[category].append(name)
    
    def add_alias(self, command_name: str, alias: str):
        """Add an alias for a command"""
        if command_name in self.commands:
            self.commands[command_name]["aliases"].append(alias)
    
    def get_command(self, name: str) -> Optional[Dict]:
        """Get command info by name or alias"""
        # Direct match
        if name in self.commands:
            return self.commands[name]
        
        # Alias match
        for cmd_name, cmd_info in self.commands.items():
            if name in cmd_info["aliases"]:
                return cmd_info
        
        return None
    
    def search_commands(self, search_term: str, limit: int = 5) -> List[str]:
        """Search for commands by name or description"""
        results = []
        search_lower = search_term.lower()
        
        for cmd_name, cmd_info in self.commands.items():
            # Check name
            if search_lower in cmd_name.lower():
                results.append((cmd_name, cmd_info, 3))  # Higher weight for name match
            
            # Check description
            elif search_lower in cmd_info["description"].lower():
                results.append((cmd_name, cmd_info, 1))  # Lower weight for description match
            
            # Check aliases
            elif any(search_lower in alias.lower() for alias in cmd_info["aliases"]):
                results.append((cmd_name, cmd_info, 2))  # Medium weight for alias match
        
        # Sort by weight and limit results
        results.sort(key=lambda x: x[2], reverse=True)
        return [r[0] for r in results[:limit]]
    
    def get_suggestions(self, user_input: str, limit: int = 3) -> List[Dict]:
        """Get command suggestions based on user input"""
        # Try to match commands
        matched_commands = self.search_commands(user_input, limit=10)
        
        if matched_commands:
            # Calculate similarity
            suggestions = []
            for cmd_name in matched_commands:
                cmd_info = self.commands[cmd_name]
                suggestions.append({
                    "name": cmd_name,
                    "description": cmd_info["description"],
                    "category": cmd_info["category"]
                })
            
            return suggestions[:limit]
        
        return []
    
    def get_all_commands_formatted(self) -> str:
        """Get all commands formatted for LLM context"""
        formatted = "Available commands (use /help for more info):\n"
        
        for category, cmd_names in self.categories.items():
            formatted += f"\n{category}:\n"
            for cmd_name in sorted(cmd_names):
                cmd_info = self.commands[cmd_name]
                formatted += f"- /{cmd_name}: {cmd_info['description']}\n"
        
        return formatted

command_db = CommandDatabase()
_location_cache = {"value": "Unknown", "city": "Unknown", "country": "Unknown", "ts": 0.0}


async def _get_runtime_location_cached() -> Tuple[str, str, str]:
    """Return cached location; refresh in background only occasionally."""
    now = time.time()
    current_location = str(getattr(integrations, "LOCATION", None) or "").strip()
    current_city = str(getattr(integrations, "CITY", None) or "").strip()
    current_country = str(getattr(integrations, "COUNTRY", None) or "").strip()

    if current_location and current_location.lower() != "unknown":
        _location_cache["value"] = current_location
        _location_cache["city"] = current_city or "Unknown"
        _location_cache["country"] = current_country or "Unknown"
        _location_cache["ts"] = now
        return _location_cache["value"], _location_cache["city"], _location_cache["country"]

    # Avoid blocking every message with network location requests.
    if now - float(_location_cache.get("ts", 0.0)) < 600:
        return (
            str(_location_cache.get("value", "Unknown") or "Unknown"),
            str(_location_cache.get("city", "Unknown") or "Unknown"),
            str(_location_cache.get("country", "Unknown") or "Unknown"),
        )

    try:
        location, city, country = await asyncio.to_thread(get_location_by_ip)
        _location_cache["value"] = location or "Unknown"
        _location_cache["city"] = city or "Unknown"
        _location_cache["country"] = country or "Unknown"
        _location_cache["ts"] = now
    except Exception:
        _location_cache["ts"] = now
    return (
        str(_location_cache.get("value", "Unknown") or "Unknown"),
        str(_location_cache.get("city", "Unknown") or "Unknown"),
        str(_location_cache.get("country", "Unknown") or "Unknown"),
    )


def initialize_command_database():
    command_db.commands.clear()
    command_db.categories.clear()
    
    # General Commands
    command_db.add_command("chat", "Chat with AI (starts new chat)", "General")
    command_db.add_command("forget", "Clear chat history (admin only)", "General")
    command_db.add_command("chat-history", "View or set how many user messages to remember per chat (1–100; set: admin only)", "General")
    command_db.add_command("dm-history", "DM only: view/set history cutoff for rolling summarization", "General")
    command_db.add_command("jarvis", "DM only: toggle adaptive Jarvis mode", "General")
    command_db.add_command("jarvis-tune", "DM only: manually trigger Jarvis tone tuning update", "General")
    command_db.add_command("fast-reply", "DM only: temporarily enable faster, shorter replies", "General")
    command_db.add_command("conversation", "Enable or disable auto-conversation in a channel", "General")
    command_db.add_command("conversation-frequency", "View or set how often the bot auto-replies in conversation channels", "General")
    command_db.add_command("status", "Show system status and bot info", "General")
    command_db.add_command("reliability", "[Admin] View or reset reliability telemetry counters", "General")
    command_db.add_command("checkwake", "Check current wake word", "General")
    command_db.add_command("sleep", "Put bot offline until /wake", "General")
    command_db.add_command("wake", "Bring bot back online", "General")
    command_db.add_command("bal", "Check OpenRouter account credits (OPENROUTER_API_KEY)", "General")
    command_db.add_command("help", "List all commands", "General")
    
    # File Analysis Commands
    command_db.add_command("analyze", "Analyze uploaded files (images, text, code, documents)", "File Analysis")
    command_db.add_command("ocr", "Extract text from images or documents (text only)", "File Analysis")
    command_db.add_command("code-review", "Review and analyze code files", "File Analysis")
    command_db.add_command("compare-files", "Compare two or more text files", "File Analysis")
    command_db.add_command("examine", "Detailed image analysis (full description)", "File Analysis")
    command_db.add_command("interrogate", "Short image answer (few sentences or bullets)", "File Analysis")
    
    # Reminder Commands
    command_db.add_command("remind", "Set a reminder", "Reminders")
    command_db.add_command("reminders", "List your active reminders", "Reminders")
    command_db.add_command("cancel-reminder", "Cancel a reminder by ID", "Reminders")
    command_db.add_command(
        "cal",
        "Build an .ics file: repeated events on chosen weekdays between two dates (local server timezone)",
        "Calendar",
    )

    # Persona Commands
    command_db.add_command("persona", "View or set global AI persona (set/remove: admin only)", "Persona")
    command_db.add_command("persona-create", "[Admin] Create a new persona", "Persona")
    
    # Model Commands
    command_db.add_command("model", "View and switch AI model (basic commands always run on local Ollama)", "Model")
    command_db.add_command("pull-model", "Install local model or validate cloud model", "Model")
    
    # Download Commands
    command_db.add_command("download", "Download media from link or last message and send to chat", "Download")
    command_db.add_command("download-limit", "[Admin] Set max download file size in MB", "Download")
    
    # Scripts Commands
    command_db.add_command("scripts", "List scripts in the scripts folder", "Scripts")
    command_db.add_command("run", "Run a script from scripts folder (now or at time)", "Scripts")
    
    # Admin Commands
    command_db.add_command("update", "Update bot from git, with buttons to restart or mark version safe", "Admin")
    command_db.add_command("rollback", "Rollback bot to safe/last working git commit", "Admin")
    command_db.add_command("purge", "Delete messages from channel", "Admin")
    command_db.add_command("restart", "Restart the bot", "Admin")
    command_db.add_command("kill", "Kill the bot", "Admin")
    command_db.add_command("whitelist", "View whitelist or set user role: /whitelist @user admin (set: admin only)", "Admin")
    command_db.add_command("setwake", "Change wake word", "Admin")
    command_db.add_command("sethome", "Set startup channel", "Admin")
    command_db.add_command("setstatus", "Change bot status", "Admin")
    command_db.add_command(
        "clone",
        "Bot mirror (on/replace) or server-wide nick clone as one user (all); off reverts (permanent admin only)",
        "Admin",
    )
    command_db.add_command("profanity", "View/edit profanity list used by clone filtering (permanent admin only)", "Admin")
    command_db.add_command("ollama-on", "Start Ollama server (admin only)", "Admin")
    command_db.add_command("ollama-off", "Stop Ollama server (admin only)", "Admin")

    # Shitpost
    command_db.add_command("ignore", "Add a word to shitpost ignore list (admin only)", "Shitpost")

    # Home Assistant Commands
    command_db.add_command("himas", "Control Home Assistant with natural language", "Home Assistant")
    command_db.add_command("explain", "Add a friendly name mapping for Home Assistant", "Home Assistant")
    command_db.add_command("listentities", "List all entity mappings", "Home Assistant")
    command_db.add_command("removeentity", "Remove an entity mapping", "Home Assistant")
    command_db.add_command("ha-status", "Check Home Assistant connection and entities", "Home Assistant")
    command_db.add_command("find-sensor", "Find and query sensors", "Home Assistant")
    
    command_db.add_alias("help", "commands")
    command_db.add_alias("help", "what can you do")
    command_db.add_alias("chat", "talk")
    command_db.add_alias("chat", "ask")
    command_db.add_alias("forget", "clear")
    command_db.add_alias("forget", "reset")
    command_db.add_alias("forget", "clear-chat")
    command_db.add_alias("status", "info")
    command_db.add_alias("status", "stats")
    command_db.add_alias("bal", "balance")
    command_db.add_alias("bal", "openrouter")
    command_db.add_alias("bal", "credits")
    command_db.add_alias("whitelist", "permissions")
    command_db.add_alias("whitelist", "users")
    command_db.add_alias("himas", "home")
    command_db.add_alias("himas", "lights")
    command_db.add_alias("himas", "smart home")
    command_db.add_alias("explain", "add")
    command_db.add_alias("explain", "map")
    command_db.add_alias("remind", "reminder")
    command_db.add_alias("reminders", "my reminders")
    command_db.add_alias("persona", "personas")
    command_db.add_alias("model", "models")
    command_db.add_alias("model", "currentmodel")
    command_db.add_alias("analyze", "analyze-file")
    command_db.add_alias("analyze", "file-analysis")
    command_db.add_alias("ocr", "extract-text")
    command_db.add_alias("ocr", "read-image")
    command_db.add_alias("code-review", "review-code")
    command_db.add_alias("compare-files", "compare")
    command_db.add_alias("download", "dl")

    # News Commands
    command_db.add_command("news", "Subscribe to news topics delivered to your DMs", "News")
    command_db.add_command("news-model", "[Admin] Set the LLM model for news summarization", "News")
    command_db.add_command("news-model-info", "Show the current news summarization model", "News")
    command_db.add_command("news-time", "Set daily quiet hours for news DMs (server local time; digest when quiet ends)", "News")
    command_db.add_command("news-source", "[Admin] Manage custom RSS sources for news topics", "News")
    command_db.add_alias("news", "subscribe")
    command_db.add_alias("news", "news feed")
    command_db.add_alias("news-time", "quiet time")
    command_db.add_alias("news-time", "pause news")

    print(f"✅ Initialized command database with {len(command_db.commands)} commands")


initialize_command_database()

class FileProcessor:
    """Process and analyze different types of files"""
    
    @staticmethod
    def get_file_type(filename: str) -> str:
        """Get the type of file based on extension"""
        ext = os.path.splitext((filename or "").lower())[1]
        return SUPPORTED_EXTENSION_TO_TYPE.get(ext, "unknown")
    
    @staticmethod
    def read_text_file(content: bytes) -> str:
        """Read text from a file"""
        try:
            # Try UTF-8 first
            return content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                # Try Latin-1 as fallback
                return content.decode('latin-1')
            except Exception:
                # Return as base64 if can't decode
                return f"[Binary file content encoded as base64]\n{base64.b64encode(content).decode('utf-8')}"
    
    @staticmethod
    def prepare_image_for_llm(image_data: bytes, filename: str) -> Dict:
        """Prepare image data for LLM with vision capabilities"""
        import base64
        mime_type = mimetypes.guess_type(filename)[0] or 'image/png'
        base64_image = base64.b64encode(image_data).decode('utf-8')
        
        return {
            "data": base64_image,
            "mime_type": mime_type,
            "filename": filename
        }
    
    @staticmethod
    def analyze_file_content(filename: str, content: bytes, file_type: str) -> str:
        """Generate analysis prompt based on file type"""
        if file_type == "text":
            text_content = FileProcessor.read_text_file(content)
            return f"Analyze the following text file '{filename}':\n\n{text_content}\n\nPlease provide: 1) Summary, 2) Key points, 3) Any issues or suggestions"
        
        elif file_type == "code":
            code_content = FileProcessor.read_text_file(content)
            return f"Review this code file '{filename}':\n\n```\n{code_content}\n```\n\nPlease provide: 1) What the code does, 2) Potential bugs or issues, 3) Suggestions for improvement, 4) Security concerns if any"
        
        elif file_type == "image":
            return f"Analyze this image file: '{filename}'. Describe what you see in detail, including any text, objects, colors, and context. If there's text in the image, transcribe it."
        
        elif file_type == "document":
            text_content = FileProcessor.read_text_file(content)
            return f"Analyze this document '{filename}':\n\n{text_content[:5000]}...\n\nPlease provide: 1) Document type, 2) Main topics, 3) Key information, 4) Summary"
        
        else:
            return f"Analyze this file '{filename}'. Provide information about what type of file it appears to be and any observations."


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def compact_dm_history_for_channel(user_id: int, channel_id: int, username: str, force: bool = False) -> Dict[str, Any]:
    """Summarize old DM messages and trim in-memory history to reduce token usage."""
    conversation = conversation_manager.get_conversation(channel_id)
    cutoff = conversation_manager.get_dm_history_cutoff(channel_id, default_cutoff=16)
    max_messages = max(8, cutoff * 2)
    if not force and len(conversation) <= max_messages:
        return {"compacted": False, "reason": "under-cutoff", "cutoff": cutoff}
    if len(conversation) <= 4:
        return {"compacted": False, "reason": "not-enough-messages", "cutoff": cutoff}

    old_messages = conversation[:-max_messages] if len(conversation) > max_messages else conversation[:-2]
    recent_messages = conversation[-max_messages:] if len(conversation) > max_messages else conversation[-2:]
    if not old_messages:
        return {"compacted": False, "reason": "nothing-to-compact", "cutoff": cutoff}

    previous_summary = conversation_manager.get_dm_summary_text(channel_id)
    def _summary_line_from_message(item: Dict[str, Any]) -> str:
        role = str(item.get("role", "user") or "user")
        content = str(item.get("content", "") or "").strip()
        if not content:
            return ""
        # Strip bulky contextual preambles that blow up tokens.
        content = re.sub(
            r"Recent messages in this channel:\n[\s\S]*?\n[\w.\- ]+ says:\s*",
            "",
            content,
            flags=re.IGNORECASE,
        ).strip()
        # Exclude likely news digests/alerts from compaction memory.
        lower = content.lower()
        if any(
            marker in lower
            for marker in [
                "news briefing",
                "daily digest",
                "breaking news",
                "top stories",
                "rss",
                "source:",
                "headline:",
            ]
        ):
            return ""
        # Keep user signal compact; assistant messages need less length.
        cap = 220 if role == "user" else 140
        if len(content) > cap:
            content = content[:cap].rstrip() + "…"
        return f"{role}: {content}"

    old_text = []
    for item in old_messages[-80:]:
        line = _summary_line_from_message(item)
        if line:
            old_text.append(line)
        if len(old_text) >= 48:
            break
    joined = "\n".join(old_text)
    if not joined.strip():
        return {"compacted": False, "reason": "empty-source", "cutoff": cutoff}

    summary_prompt = (
        "You are maintaining compact long-term memory for a Discord DM assistant.\n"
        "Create a concise summary that preserves:\n"
        "1) user preferences and dislikes,\n"
        "2) key ongoing tasks or commitments,\n"
        "3) important context/events that future replies need.\n"
        "Output plain text bullets only, max 130 words.\n\n"
        f"Existing memory summary:\n{previous_summary or '(none)'}\n\n"
        f"New older messages to merge:\n{joined}"
    )
    messages = [{"role": "user", "content": summary_prompt}]
    model_info = model_manager.get_user_model_info(user_id)
    requested_model = model_info.get("model", "qwen2.5:7b")
    provider = model_info.get("provider", "local")
    _, summary = await _try_models_with_fallback(
        requested_model=requested_model,
        messages=messages,
        images=False,
        provider=provider,
    )
    summary = _clean_response(summary or "")
    if not summary or summary.startswith("Error:") or summary.startswith("⚠️"):
        return {"compacted": False, "reason": "summary-failed", "cutoff": cutoff}

    conversation_manager.append_dm_summary(channel_id, summary, merged_messages=len(old_messages))
    conversation_manager.replace_conversation(channel_id, recent_messages)
    conversation_manager.save()
    return {
        "compacted": True,
        "cutoff": cutoff,
        "merged_messages": len(old_messages),
        "remaining_messages": len(recent_messages),
    }


async def plan_command_from_text(user_id: int, message_text: str, command_schema: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Infer a slash command plan from natural language in DMs."""
    if not message_text or not command_schema:
        return {"should_execute": False}
    schema_json = json.dumps(command_schema, ensure_ascii=True)
    planner_prompt = (
        "Convert the user's natural language into a bot command plan.\n"
        "Return strict JSON only with keys:\n"
        "should_execute (bool), command (string), arguments (object), reason (string), risk (safe|risky|dangerous).\n"
        "Rules:\n"
        "- should_execute=false when the message is general chat, question, or unclear.\n"
        "- Use only command names from schema.\n"
        "- Fill only known argument names for that command.\n"
        "- Keep arguments as plain strings/numbers/booleans.\n"
        "- risk must be dangerous for restart/kill/update/purge/clone/run/profanity/setwake/sethome/setstatus/whitelist.\n\n"
        f"Command schema:\n{schema_json}\n\n"
        f"User message:\n{message_text}"
    )
    model_info = model_manager.get_user_model_info(user_id)
    requested_model = model_info.get("model", "qwen2.5:7b")
    provider = model_info.get("provider", "local")
    _, raw = await _try_models_with_fallback(
        requested_model=requested_model,
        messages=[{"role": "user", "content": planner_prompt}],
        images=False,
        provider=provider,
    )
    parsed = _extract_json_object(raw or "")
    if not parsed:
        return {"should_execute": False}
    plan = {
        "should_execute": bool(parsed.get("should_execute", False)),
        "command": str(parsed.get("command", "") or "").strip().lower(),
        "arguments": parsed.get("arguments", {}) if isinstance(parsed.get("arguments", {}), dict) else {},
        "reason": str(parsed.get("reason", "") or "").strip(),
        "risk": str(parsed.get("risk", "safe") or "safe").strip().lower(),
    }
    return plan

async def ask_llm(
    user_id,
    channel_id,
    message_text,
    username,
    is_continuation=False,
    platform="discord",
    chat_context=None,
    attachments=None,
    is_dm=False,
    fast_reply=False,
):
    """Main LLM interface for all platforms with file support"""
    # Get system info
    date, time = update_system_time_date()
    location, city, country = await _get_runtime_location_cached()
    
    # Get persona
    persona_name = persona_manager.get_user_persona(user_id)
    system_prompt = persona_manager.get_persona(persona_name)
    
    # Get model
    model_info = model_manager.get_user_model_info(user_id)
    requested_model = model_info.get("model", "llama3.2:1b")
    provider = model_info.get("provider", "local")
    
    # Check if user is asking for commands or help
    user_message_lower = message_text.lower()
    is_command_related = any(keyword in user_message_lower for keyword in [
        "command", "help", "what can you do", "how do i", "how to", 
        "show me", "list", "available", "options", "features"
    ])
    
    # Get command suggestions if needed
    command_suggestions = ""
    if is_command_related or "?" in user_message_lower:
        suggestions = command_db.get_suggestions(message_text, limit=3)
        if suggestions:
            command_suggestions = "\n\nIf you're looking for commands, here are some suggestions:\n"
            for suggestion in suggestions:
                command_suggestions += f"- `/{suggestion['name']}`: {suggestion['description']}\n"
            command_suggestions += "\nUse `/help` to see all available commands or `/help [search]` to search."
    
    # Process attachments if any
    attachment_context = ""
    images_data = []
    
    if attachments:
        attachment_context = "\n\nUser has uploaded the following files:\n"
        for i, attachment in enumerate(attachments, 1):
            file_type = FileProcessor.get_file_type(attachment['filename'])
            attachment_context += f"{i}. {attachment['filename']} ({file_type} file, {len(attachment['data'])} bytes)\n"
            
            # Prepare images for vision models
            if file_type == "image":
                try:
                    img_data = FileProcessor.prepare_image_for_llm(attachment['data'], attachment['filename'])
                    images_data.append(img_data)
                except Exception as e:
                    attachment_context += f"   ⚠️ Failed to process image: {str(e)}\n"
    
    # Prefix every user message with who said it (no wake word in content)
    if platform == "discord" and chat_context:
        formatted_message = _format_discord_message(username, message_text, chat_context)
    else:
        formatted_message = f"{username} says: {message_text}"
    persisted_user_message = f"{username} says: {message_text}"
    
    # Add attachment context to message
    if attachment_context:
        formatted_message += attachment_context
    
    enhanced = get_enhanced_prompt(
        "chat",
        date=date,
        time=time,
        location=location,
        platform=platform.capitalize(),
        command_count=len(command_db.commands),
        command_list=command_db.get_all_commands_formatted(),
        command_suggestions=command_suggestions or "",
    )
    enhanced_system_prompt = f"{system_prompt}\n\n{enhanced}"
    if is_dm and jarvis_manager.is_enabled(user_id):
        jarvis_profile_prompt = jarvis_manager.get_profile_prompt(user_id)
        if jarvis_profile_prompt:
            enhanced_system_prompt = f"{enhanced_system_prompt}\n\n{jarvis_profile_prompt}"
        enhanced_system_prompt += (
            "\n\nJarvis mode is ON for this DM. Keep your tone natural and conversational. "
            "Avoid formal assistant phrasing, avoid stiff closers, and do not call the user 'sir' or similar titles. "
            "Be proactive, keep continuity, and match the user's style."
        )
    elif is_dm:
        enhanced_system_prompt += (
            "\n\nThis is a direct DM chat. Use a relaxed, natural tone. "
            "Keep it human and concise, and avoid overly formal phrasing."
        )
    if fast_reply:
        enhanced_system_prompt += (
            "\n\nFast reply mode is enabled. Be concise and quick: "
            "usually 1-3 short sentences unless the user asks for details."
        )
    
    # Prepare conversation history (channel-based: one thread per channel, last 5 turns)
    if is_continuation:
        if is_dm:
            try:
                await compact_dm_history_for_channel(user_id, channel_id, username, force=False)
            except Exception:
                pass
        history = conversation_manager.get_conversation(channel_id)
        if not history:
            history = [{"role": "system", "content": enhanced_system_prompt}]
    else:
        conversation_manager.clear_conversation(channel_id)
        history = [{"role": "system", "content": enhanced_system_prompt}]
    
    # Build messages
    messages = history.copy()
    if is_dm and len(messages) > 24:
        # DM responses should stay snappy; summaries preserve long-term context.
        messages = messages[-24:]
    if is_dm and is_continuation:
        dm_summary = conversation_manager.get_dm_summary_text(channel_id)
        if dm_summary:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "Long-term memory summary from older DM messages:\n"
                        f"{dm_summary}"
                    ),
                },
            )
    
    # If we have images, always attach them so fallback can use a vision model
    if images_data:
        user_message = {
            "role": "user",
            "content": formatted_message,
            "images": [img["data"] for img in images_data]
        }
        messages.append(user_message)
    else:
        messages.append({"role": "user", "content": formatted_message})
    
    request_options = None
    if fast_reply:
        request_options = {"num_predict": 220, "temperature": 0.55}

    # Try models with fallback
    final_model, response_text = await _try_models_with_fallback(
        requested_model,
        messages,
        images=bool(images_data),
        provider=provider,
        request_options=request_options,
    )
    
    # Clean response
    response_text = _clean_response(response_text)
    
    # Store conversation if successful (channel-based; user identity is in formatted_message)
    if response_text and not response_text.startswith("Error:"):
        conversation_manager.add_message(channel_id, "user", persisted_user_message)
        conversation_manager.add_message(channel_id, "assistant", response_text)
        conversation_manager.save()
        
    
    return response_text

def _clean_response(text):
    """Clean up LLM response"""
    if not text:
        return ""
    
    # Remove unnecessary prefixes
    text = re.sub(r'^assistant:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^ai:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^bot:\s*', '', text, flags=re.IGNORECASE)
    # Don't parrot "X says:" or "X is asking" at start of reply
    text = re.sub(r'^[\w.]+\s+(?:says|is asking):\s*', '', text, flags=re.IGNORECASE)
    
    # Clean up extra whitespace
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    
    return text.strip()


async def ask_llm_shitpost(user_id: int, word: str) -> str:
    """One-shot LLM reply for shitpost: max 2 words confirming/continuing the word. Returns empty on error."""
    system_prompt = get_enhanced_prompt("shitpost")
    requested_model = model_manager.get_last_local_model(user_id, refresh_local=True)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": word},
    ]
    try:
        _, response_text = await _try_models_with_fallback(
            requested_model,
            messages,
            images=False,
            provider="local",
        )
        cleaned = _clean_response(response_text or "")
        if cleaned.startswith("Error:"):
            return ""
        parts = cleaned.split()
        return " ".join(parts[:2]) if parts else ""
    except Exception:
        return ""


def _format_discord_message(username, message, context):
    """Format Discord message with chat context; prefix with who is speaking."""
    if not context or len(context) == 0:
        return f"{username} says: {message}"
    context_str = "\nRecent messages in this channel:\n"
    for msg in context[-10:]:
        context_str += f"{msg['author']}: {msg['content']}\n"
    return f"{context_str}\n{username} says: {message}"

_vision_model_cache = None


def clear_vision_model_cache() -> None:
    """Call when model list changes (e.g. after /pull-model or remove) so vision is re-resolved."""
    global _vision_model_cache
    _vision_model_cache = None


# Name patterns that typically indicate vision-capable models (Ollama)
VISION_NAME_PATTERNS = ("llava", "llama3.2", "llama3.1", "pixtral", "minicpm-v", "vision", "moondream", "bakllava", "llava-phi", "nano-llava")


def _is_vision_capable(model_name: str) -> bool:
    """Heuristic: model name suggests vision support."""
    if not model_name:
        return False
    lower = model_name.lower()
    return any(p in lower for p in VISION_NAME_PATTERNS)


def _format_vision_help_message() -> str:
    """Build message listing available models with vision-capable marked. Call when image request fails."""
    models = model_manager.list_all_models(refresh_local=True)
    if not models:
        return "No vision-capable model could process this image. No models are available. Use **/pull-model local model_name** to install one (e.g. `llava` or `llama3.2:3b`)."
    lines = ["No vision-capable model could process this image. Use **/model** to switch or **/pull-model local model_name** to install one.", "", "**Available models** (✅ = typically vision-capable):", ""]
    for m in models:
        mark = "✅" if _is_vision_capable(m) else "○"
        lines.append(f"{mark} `{m}`")
    return "\n".join(lines)


async def _test_model_vision(model_name: str, messages: list) -> bool:
    try:
        r = await _make_ollama_request(model_name, messages)
        return bool(r and not r.startswith("Error:"))
    except Exception:
        return False

async def _resolve_vision_model(requested_model: str, messages: list) -> Optional[str]:
    global _vision_model_cache
    if _vision_model_cache is not None:
        return _vision_model_cache
    chain = [requested_model] + [m for m in _get_fallback_chain() if m != requested_model]
    for model in chain:
        if await _test_model_vision(model, messages):
            _vision_model_cache = model
            return model
    return None

def _to_openrouter_messages(messages: list) -> list:
    converted = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        images = msg.get("images") or []
        if images:
            parts = []
            if content:
                parts.append({"type": "text", "text": str(content)})
            for base64_img in images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_img}"},
                    }
                )
            converted.append({"role": role, "content": parts})
        else:
            converted.append({"role": role, "content": str(content)})
    return converted


async def _make_openrouter_request(model_name: str, messages: list, max_tokens: Optional[int] = None) -> str:
    def _read_raw_dotenv(path: str = ".env") -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not os.path.isfile(path):
            return out
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.lower().startswith("export "):
                        line = line[7:].strip()
                    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
                    if not m:
                        continue
                    key, val = m.group(1), m.group(2).strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    else:
                        if " #" in val:
                            val = val.split(" #", 1)[0].rstrip()
                    val = val.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
                    val = "".join(ch for ch in val if ch.isprintable())
                    val = re.sub(r"\s+", "", val)
                    if val:
                        out[key] = val
        except Exception:
            return {}
        return out

    def _mask_key(k: str) -> str:
        text = str(k or "")
        if len(text) <= 10:
            return "***"
        return f"{text[:6]}...{text[-4:]}"

    def _candidate_openrouter_keys() -> List[str]:
        keys: List[str] = []
        dotenv_raw = _read_raw_dotenv(".env")
        for k in [
            OPENROUTER_CHAT_API_KEY,
            getattr(integrations, "OPENROUTER_API_KEY", ""),
            getattr(integrations, "OPENROUTER_MANAGEMENT_API_KEY", ""),
            getattr(integrations, "OPENROUTER_LEGACY_API_KEY", ""),
            os.environ.get("OPENROUTER_CHAT_API_KEY", ""),
            os.environ.get("OPENROUTER_API_KEY", ""),
            os.environ.get("OPENROUTER_MANAGEMENT_API_KEY", ""),
            dotenv_raw.get("OPENROUTER_CHAT_API_KEY", ""),
            dotenv_raw.get("OPENROUTER_API_KEY", ""),
            dotenv_raw.get("OPENROUTER_MANAGEMENT_API_KEY", ""),
        ]:
            val = str(k or "").strip()
            if val and val not in keys:
                keys.append(val)
        return keys

    candidate_keys = _candidate_openrouter_keys()
    if not candidate_keys:
        return "Error: OPENROUTER_CHAT_API_KEY is not configured."
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model_name,
        "messages": _to_openrouter_messages(messages),
        "temperature": 0.7,
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max(64, min(1024, max_tokens)))
    def _extract_openrouter_error(status_code: int, body: dict, raw_text: str) -> str:
        error_obj = body.get("error") if isinstance(body, dict) else {}
        if not isinstance(error_obj, dict):
            error_obj = {}
        message = str(error_obj.get("message", "")).strip()
        metadata = error_obj.get("metadata") if isinstance(error_obj.get("metadata"), dict) else {}
        raw_meta = str(metadata.get("raw", "")).strip()
        detail = raw_meta or message or raw_text

        if status_code == 429:
            return (
                f"Rate limited for `{model_name}` on OpenRouter. "
                "Retry in a moment, switch to another cloud model, or use a local model."
            )
        if status_code == 401:
            return (
                "OpenRouter authentication failed. "
                "Check OPENROUTER_CHAT_API_KEY (chat key). "
                "If /bal works but chat fails, you likely set only a management key."
                f"{(' Details: ' + detail[:180]) if detail else ''}"
            )
        if status_code == 402:
            return "OpenRouter credits required for this request. Top up credits or choose another model."
        if detail:
            return f"OpenRouter request failed ({status_code}): {detail[:220]}"
        return f"OpenRouter request failed ({status_code})."

    last_auth_error = ""
    for idx, key in enumerate(candidate_keys):
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/hophaver/dubot",
            "X-Title": "dubot",
        }
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, headers=headers, timeout=90)
            body = {}
            try:
                body = response.json()
            except ValueError:
                body = {}

            # One automatic retry for temporary rate limits
            if response.status_code == 429:
                await asyncio.sleep(2)
                response = await asyncio.to_thread(requests.post, url, json=payload, headers=headers, timeout=90)
                try:
                    body = response.json()
                except ValueError:
                    body = {}

            if response.status_code == 401:
                last_auth_error = _extract_openrouter_error(response.status_code, body, response.text or "")
                home_log.log_sync(
                    f"⚠️ OpenRouter 401 for model `{model_name}` with key {_mask_key(key)} "
                    f"(candidate {idx + 1}/{len(candidate_keys)}): {last_auth_error}"
                )
                # Retry with next candidate key if available.
                if idx < len(candidate_keys) - 1:
                    continue
                return f"Error: {last_auth_error}"

            if response.status_code != 200:
                return f"Error: {_extract_openrouter_error(response.status_code, body, response.text or '')}"

            choices = body.get("choices") or []
            if not choices:
                return "Error: No choices returned by OpenRouter."
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = message.get("content", "")
            if isinstance(content, list):
                text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
                content = "".join(text_parts)
            return str(content).strip() or "No response."
        except requests.exceptions.Timeout:
            return "Error: Request timed out"
        except Exception as e:
            return f"Error: {str(e)}"
    if last_auth_error:
        return f"Error: {last_auth_error}"
    return "Error: OpenRouter request failed."


async def _try_models_with_fallback(requested_model, messages, images=False, provider="local", request_options: Optional[Dict[str, Any]] = None):
    provider = (provider or "local").strip().lower()
    if provider == "cloud":
        max_tokens = None
        if isinstance(request_options, dict) and request_options.get("num_predict") is not None:
            try:
                max_tokens = int(request_options.get("num_predict"))
            except (TypeError, ValueError):
                max_tokens = None
        response = await _make_openrouter_request(requested_model, messages, max_tokens=max_tokens)
        if response and not response.startswith("Error:"):
            return requested_model, response
        return requested_model, f"⚠️ Cloud model unavailable: {response}"

    if images:
        vision_model = await _resolve_vision_model(requested_model, messages)
        if vision_model:
            models_to_try = [vision_model]
        else:
            available = model_manager.list_all_models(refresh_local=True)
            if available:
                models_to_try = [requested_model] + [m for m in available if m != requested_model]
            else:
                models_to_try = [requested_model] + _get_fallback_chain()
    else:
        models_to_try = [requested_model] + _get_fallback_chain()

    models_to_try = list(dict.fromkeys(models_to_try))
    for model_name in models_to_try:
        response = await _make_ollama_request(model_name, messages, request_options=request_options)

        if response and not response.startswith("Error:"):
            return model_name, response

        if "404" in response or "not found" in response.lower():
            continue

        home_log.log_sync(f"Error with model {model_name}: {response}")

    if images:
        return requested_model, _format_vision_help_message()
    return requested_model, "⚠️ All models are unavailable. Please check your Ollama server."

async def _make_ollama_request(model_name, messages, request_options: Optional[Dict[str, Any]] = None):
    """Make request to Ollama API"""
    endpoints = [
        OLLAMA_URL,
        "http://localhost:11434",
        "http://127.0.0.1:11434",
    ]

    def _is_transient_status(code: int) -> bool:
        return code in {408, 425, 429, 500, 502, 503, 504}

    has_images = any(isinstance(msg, dict) and msg.get("images") for msg in messages)
    if has_images:
        formatted_messages = []
        for msg in messages:
            if msg.get("role") == "user" and msg.get("images"):
                formatted_messages.append(
                    {
                        "role": "user",
                        "content": msg["content"],
                        "images": msg["images"],
                    }
                )
            else:
                formatted_messages.append(
                    {
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", ""),
                    }
                )
        data = {"model": model_name, "messages": formatted_messages, "stream": False}
    else:
        options = {"temperature": 0.7, "num_predict": 1024}
        if isinstance(request_options, dict):
            for k in ("temperature", "num_predict"):
                if request_options.get(k) is not None:
                    options[k] = request_options.get(k)
        data = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": options,
        }

    for base_url in endpoints:
        url = f"{base_url}/api/chat"

        for attempt in range(3):
            try:
                # Run blocking I/O in a thread so Discord event loop stays responsive.
                response = await asyncio.to_thread(requests.post, url, json=data, timeout=75)

                if response.status_code == 404:
                    break

                if response.status_code != 200:
                    if _is_transient_status(response.status_code) and attempt < 2:
                        reliability_telemetry.increment("llm_retries")
                        home_log.log_sync(
                            f"⚠️ LLM transient HTTP {response.status_code} for model `{model_name}` "
                            f"(attempt {attempt + 1}/3). "
                            f"{reliability_telemetry.format_snapshot('Counters')}"
                        )
                        await asyncio.sleep(1 + attempt)
                        continue
                    error_text = (response.text or "")[:140]
                    error_count = reliability_telemetry.increment("llm_errors")
                    home_log.log_sync(
                        f"🔴 LLM HTTP error {response.status_code} for model `{model_name}` "
                        f"(error #{error_count}): {error_text}"
                    )
                    return f"Error {response.status_code}: {error_text}"

                try:
                    result = response.json()
                except ValueError:
                    if attempt < 2:
                        reliability_telemetry.increment("llm_retries")
                        home_log.log_sync(
                            f"⚠️ LLM returned non-JSON response for model `{model_name}` "
                            f"(attempt {attempt + 1}/3)."
                        )
                        await asyncio.sleep(1 + attempt)
                        continue
                    error_count = reliability_telemetry.increment("llm_errors")
                    home_log.log_sync(
                        f"🔴 LLM returned invalid JSON for model `{model_name}` "
                        f"(error #{error_count})."
                    )
                    return "Error: Invalid JSON response from Ollama."
                return result.get("message", {}).get("content", "No response.")

            except requests.exceptions.ConnectionError:
                if attempt < 2:
                    reliability_telemetry.increment("llm_retries")
                    home_log.log_sync(
                        f"⚠️ LLM connection error for model `{model_name}` "
                        f"(attempt {attempt + 1}/3). "
                        f"{reliability_telemetry.format_snapshot('Counters')}"
                    )
                    await asyncio.sleep(1 + attempt)
                    continue
                break
            except requests.exceptions.Timeout:
                if attempt < 2:
                    reliability_telemetry.increment("llm_retries")
                    home_log.log_sync(
                        f"⚠️ LLM timeout for model `{model_name}` "
                        f"(attempt {attempt + 1}/3). "
                        f"{reliability_telemetry.format_snapshot('Counters')}"
                    )
                    await asyncio.sleep(1 + attempt)
                    continue
                timeout_count = reliability_telemetry.increment("llm_timeouts")
                home_log.log_sync(
                    f"🔴 LLM timeout after retries for model `{model_name}` "
                    f"(timeout #{timeout_count}). "
                    f"{reliability_telemetry.format_snapshot('Counters')}"
                )
                return "Error: Request timed out"
            except Exception as e:
                if attempt < 2:
                    reliability_telemetry.increment("llm_retries")
                    home_log.log_sync(
                        f"⚠️ LLM unexpected error for model `{model_name}`: {str(e)[:200]} "
                        f"(attempt {attempt + 1}/3)."
                    )
                    await asyncio.sleep(1 + attempt)
                    continue
                error_count = reliability_telemetry.increment("llm_errors")
                home_log.log_sync(
                    f"🔴 LLM fatal error for model `{model_name}` (error #{error_count}): {str(e)[:300]}"
                )
                return f"Error: {str(e)}"

    error_count = reliability_telemetry.increment("llm_errors")
    home_log.log_sync(
        f"🔴 LLM connection failed for all Ollama endpoints (error #{error_count}) for model `{model_name}`. "
        f"{reliability_telemetry.format_snapshot('Counters')}"
    )
    return "Error: Cannot connect to Ollama server."

async def validate_and_set_model(user_id, provider, model_name):
    provider = (provider or "local").strip().lower()
    if provider not in {"local", "cloud"}:
        return False, "Provider must be 'local' or 'cloud'."
    test_messages = [{"role": "user", "content": "Test"}]
    if provider == "cloud":
        response = await _make_openrouter_request(model_name, test_messages)
    else:
        response = await _make_ollama_request(model_name, test_messages)
    if response and not response.startswith("Error:"):
        model_manager.set_user_model(user_id, model_name, provider=provider)
        return True, f"Model '{model_name}' set ({provider})."
    return False, f"Cannot use model '{model_name}'. {response}"

async def analyze_file(user_id: int, channel_id: int, filename: str, file_data: bytes, user_prompt: str = "", username: str = "", vision_mode: str = "concise", return_only_text: bool = False) -> str:
    """vision_mode: concise (short), examine (detailed), interrogate (very short). return_only_text: if True, return only extracted/response text (no header)."""
    date, time = update_system_time_date()
    
    # Get persona and model
    persona_name = persona_manager.get_user_persona(user_id)
    system_prompt = persona_manager.get_persona(persona_name)
    model_info = model_manager.get_user_model_info(user_id)
    model_name = model_info.get("model", "llama3.2:3b")
    provider = model_info.get("provider", "local")
    
    # Determine file type
    file_type = FileProcessor.get_file_type(filename)
    
    # If user prompt is about OCR or text extraction, adjust prompt
    user_prompt_lower = user_prompt.lower()
    is_ocr_request = any(keyword in user_prompt_lower for keyword in 
                        ["ocr", "extract text", "read text", "what does it say", "what's written", "transcribe"])
    
    # Generate appropriate prompt
    if user_prompt:
        analysis_prompt = user_prompt
    else:
        if file_type == "image":
            if is_ocr_request:
                analysis_prompt = "Extract ALL text from this image. Be thorough and accurate. Format the text clearly, preserving line breaks and structure when possible. If there's no text, say 'No text found in the image.'"
            else:
                analysis_prompt = "Describe this image in detail. Be objective and factual. Describe what you actually see, not assumptions. Include: objects, people, animals, text, colors, setting, actions if any. If it's a screenshot or contains text, also extract and transcribe the text."
        else:
            analysis_prompt = FileProcessor.analyze_file_content(filename, file_data, file_type)
    
    # Prepare image if needed
    images_data = []
    if file_type == "image":
        try:
            img_data = FileProcessor.prepare_image_for_llm(file_data, filename)
            images_data.append(img_data)
        except Exception as e:
            return f"⚠️ Failed to process image: {str(e)}"
    
    if file_type == "image":
        key = {"concise": "file_analysis_image_concise", "examine": "file_analysis_image_examine", "interrogate": "file_analysis_image_interrogate"}.get(vision_mode, "file_analysis_image")
        img_prompt = get_enhanced_prompt(key)
        enhanced_system_prompt = f"{system_prompt}\n\n{img_prompt}"
    else:
        enhanced_system_prompt = f"{system_prompt}\n\n{get_enhanced_prompt('file_analysis_other', date=date, time=time, filename=filename, file_type=file_type)}"
    
    # Prepare messages
    messages = [
        {"role": "system", "content": enhanced_system_prompt},
    ]
    
    # Add image data if present
    if images_data:
        user_message = {
            "role": "user",
            "content": analysis_prompt,
            "images": [img["data"] for img in images_data]
        }
        messages.append(user_message)
    else:
        # For non-image files, include content
        if file_type in ["text", "code", "document"]:
            try:
                text_content = FileProcessor.read_text_file(file_data)
                # Limit content length to avoid token limits
                if len(text_content) > 8000:
                    text_content = text_content[:8000] + "\n\n...[Content truncated due to length]..."
                full_prompt = f"{analysis_prompt}\n\nFile Content:\n{text_content}"
                messages.append({"role": "user", "content": full_prompt})
            except Exception as e:
                messages.append({"role": "user", "content": f"{analysis_prompt}\n\nError reading file: {str(e)}"})
        else:
            messages.append({"role": "user", "content": analysis_prompt})
    
    # Get response - use same vision fallback as ask_llm (user's model + fallbacks)
    if file_type == "image":
        model_used, response = await _try_models_with_fallback(model_name, messages, images=True, provider=provider)
        if response and response.startswith("⚠️"):
            pass  # keep warning message
        else:
            model_name = model_used
    else:
        if provider == "cloud":
            response = await _make_openrouter_request(model_name, messages)
        else:
            response = await _make_ollama_request(model_name, messages)
    
    # Clean and format response
    response = _clean_response(response)
    
    # Store in conversation history
    conversation_manager.add_message(channel_id, "user", f"{username} says: [File Upload: {filename}] {user_prompt or 'Analyze this file'}")
    conversation_manager.add_message(channel_id, "assistant", response)
    conversation_manager.save()
    
    if return_only_text:
        return response
    final_response = f"📄 **File Analysis: {filename}**\n"
    final_response += f"*Type: {file_type.upper()} | Size: {len(file_data):,} bytes*\n\n"
    final_response += response
    return final_response

async def compare_files(user_id: int, channel_id: int, files: List[Dict], user_prompt: str = "", username: str = "") -> str:
    """Compare multiple text files"""
    # Get system info
    date, time = update_system_time_date()
    
    # Get persona and model
    persona_name = persona_manager.get_user_persona(user_id)
    system_prompt = persona_manager.get_persona(persona_name)
    model_info = model_manager.get_user_model_info(user_id)
    model_name = model_info.get("model", "llama3.2:3b")
    provider = model_info.get("provider", "local")
    
    # Extract text from all files
    file_contents = []
    for file_info in files:
        try:
            text_content = FileProcessor.read_text_file(file_info['data'])
            # Limit content length
            if len(text_content) > 4000:
                text_content = text_content[:4000] + "\n...[Content truncated]"
            file_contents.append({
                'filename': file_info['filename'],
                'content': text_content,
                'type': FileProcessor.get_file_type(file_info['filename'])
            })
        except Exception as e:
            file_contents.append({
                'filename': file_info['filename'],
                'content': f"Error reading file: {str(e)}",
                'type': 'error'
            })
    
    # Prepare comparison prompt
    if user_prompt:
        analysis_prompt = user_prompt
    else:
        analysis_prompt = "Compare these files. Identify similarities, differences, and provide an overall analysis."
    
    # Build file content for prompt
    file_content_str = ""
    for i, file_info in enumerate(file_contents, 1):
        file_content_str += f"\n\n--- File {i}: {file_info['filename']} ---\n"
        file_content_str += file_info['content']
    
    enhanced_system_prompt = f"{system_prompt}\n\n{get_enhanced_prompt('compare_files', date=date, time=time)}"
    
    messages = [
        {"role": "system", "content": enhanced_system_prompt},
        {"role": "user", "content": f"{analysis_prompt}\n\nFiles to compare:{file_content_str}"}
    ]
    
    if provider == "cloud":
        response = await _make_openrouter_request(model_name, messages)
    else:
        response = await _make_ollama_request(model_name, messages)
    response = _clean_response(response)
    
    # Format response
    header = f"🔍 **File Comparison: {len(files)} files**\n"
    for file_info in files:
        header += f"- {file_info['filename']} ({len(file_info['data']):,} bytes)\n"
    
    final_response = header + "\n" + response
    
    # Store in conversation history
    file_names = ", ".join([f['filename'] for f in files])
    conversation_manager.add_message(channel_id, "user", f"{username} says: [File Comparison: {file_names}] {user_prompt or 'Compare these files'}")
    conversation_manager.add_message(channel_id, "assistant", response)
    conversation_manager.save()
    
    return final_response
