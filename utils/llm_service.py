import re
import json
import os
import asyncio
import base64
import mimetypes
import requests
from typing import Dict, List, Optional, Tuple, Any
from integrations import OLLAMA_URL, update_system_time_date, get_location_by_ip
from conversations import conversation_manager
from personas import persona_manager
from models import model_manager
from utils import home_log

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


def initialize_command_database():
    command_db.commands.clear()
    command_db.categories.clear()
    
    # General Commands
    command_db.add_command("chat", "Chat with AI (starts new chat)", "General")
    command_db.add_command("forget", "Clear your chat history", "General")
    command_db.add_command("chat-history", "View or set how many user messages to remember per chat (1–100; set: admin only)", "General")
    command_db.add_command("status", "Show system status and bot info", "General")
    command_db.add_command("checkwake", "Check current wake word", "General")
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
    
    # Persona Commands
    command_db.add_command("persona", "View or set global AI persona (Confirm/Remove: admin only)", "Persona")
    command_db.add_command("persona-create", "[Admin] Create a new persona", "Persona")
    
    # Model Commands
    command_db.add_command("model", "View and switch Ollama model (buttons: admin only)", "Model")
    command_db.add_command("pull-model", "Download new Ollama model", "Model")
    
    # Download Commands
    command_db.add_command("download", "Download media from link or last message and send to chat", "Download")
    command_db.add_command("download-limit", "[Admin] Set max download file size in MB", "Download")
    
    # Scripts Commands
    command_db.add_command("scripts", "List scripts in the scripts folder", "Scripts")
    command_db.add_command("run", "Run a script from scripts folder (now or at time)", "Scripts")
    
    # Admin Commands
    command_db.add_command("update", "Update bot from git repo", "Admin")
    command_db.add_command("purge", "Delete messages from channel", "Admin")
    command_db.add_command("restart", "Restart the bot", "Admin")
    command_db.add_command("kill", "Kill the bot", "Admin")
    command_db.add_command("whitelist", "View and manage whitelist (roles and users)", "Admin")
    command_db.add_command("setwake", "Change wake word", "Admin")
    command_db.add_command("sethome", "Set startup channel", "Admin")
    command_db.add_command("setstatus", "Change bot status", "Admin")
    
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
    
    print(f"✅ Initialized command database with {len(command_db.commands)} commands")


initialize_command_database()

class FileProcessor:
    """Process and analyze different types of files"""
    
    @staticmethod
    def get_file_type(filename: str) -> str:
        """Get the type of file based on extension"""
        for file_type, extensions in SUPPORTED_FILE_TYPES.items():
            for ext in extensions:
                if filename.lower().endswith(ext):
                    return file_type
        return "unknown"
    
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

async def ask_llm(user_id, channel_id, message_text, username, is_continuation=False, platform="discord", chat_context=None, attachments=None):
    """Main LLM interface for all platforms with file support"""
    # Get system info
    date, time = update_system_time_date()
    location, city, country = get_location_by_ip()
    
    # Get persona
    persona_name = persona_manager.get_user_persona(user_id)
    system_prompt = persona_manager.get_persona(persona_name)
    
    # Get model
    model_info = model_manager.get_user_model_info(user_id)
    requested_model = model_info.get("model", "llama3.2:1b")
    
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
    
    # Prepare conversation history (channel-based: one thread per channel, last 5 turns)
    if is_continuation:
        history = conversation_manager.get_conversation(channel_id)
        if not history:
            history = [{"role": "system", "content": enhanced_system_prompt}]
    else:
        conversation_manager.clear_conversation(channel_id)
        history = [{"role": "system", "content": enhanced_system_prompt}]
    
    # Build messages
    messages = history.copy()
    
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
    
    # Try models with fallback
    final_model, response_text = await _try_models_with_fallback(requested_model, messages, images=bool(images_data))
    
    # Clean response
    response_text = _clean_response(response_text)
    
    # Store conversation if successful (channel-based; user identity is in formatted_message)
    if response_text and not response_text.startswith("Error:"):
        conversation_manager.add_message(channel_id, "user", formatted_message)
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

def _format_discord_message(username, message, context):
    """Format Discord message with chat context; prefix with who is speaking."""
    if not context or len(context) == 0:
        return f"{username} says: {message}"
    context_str = "\nRecent messages in this channel:\n"
    for msg in context[-3:]:
        context_str += f"{msg['author']}: {msg['content']}\n"
    return f"{context_str}\n{username} says: {message}"

_vision_model_cache = None

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

async def _try_models_with_fallback(requested_model, messages, images=False):
    if images:
        vision_model = await _resolve_vision_model(requested_model, messages)
        if vision_model:
            models_to_try = [vision_model]
        else:
            models_to_try = [requested_model] + _get_fallback_chain()
    else:
        models_to_try = [requested_model] + _get_fallback_chain()
    
    for model_name in models_to_try:
        if model_name in models_to_try[:models_to_try.index(model_name)]:
            continue
            
        response = await _make_ollama_request(model_name, messages)
        
        if response and not response.startswith("Error:"):
            return model_name, response
        
        if "404" in response or "not found" in response.lower():
            continue
            
        home_log.log_sync(f"Error with model {model_name}: {response}")
    
    return requested_model, "⚠️ All models are unavailable. Please check your Ollama server."

async def _make_ollama_request(model_name, messages):
    """Make request to Ollama API"""
    endpoints = [
        OLLAMA_URL,
        "http://localhost:11434",
        "http://127.0.0.1:11434",
    ]
    
    for base_url in endpoints:
        url = f"{base_url}/api/chat"
        
        # Check if any message has images
        has_images = False
        for msg in messages:
            if isinstance(msg, dict) and msg.get("images"):
                has_images = True
                break
        
        # Prepare data
        if has_images:
            # For vision models, we need to format messages with images
            formatted_messages = []
            for msg in messages:
                if msg.get("role") == "user" and msg.get("images"):
                    # Ollama vision format
                    formatted_messages.append({
                        "role": "user",
                        "content": msg["content"],
                        "images": msg["images"]
                    })
                else:
                    formatted_messages.append({
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", "")
                    })
            
            data = {
                "model": model_name,
                "messages": formatted_messages,
                "stream": False
            }
        else:
            data = {
                "model": model_name,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 1024  # Increased for file analysis
                }
            }
        
        try:
            response = requests.post(url, json=data, timeout=60)
            
            if response.status_code == 404:
                continue
            
            if response.status_code != 200:
                error_text = response.text[:100]
                return f"Error {response.status_code}: {error_text}"
            
            result = response.json()
            return result.get("message", {}).get("content", "No response.")
            
        except requests.exceptions.ConnectionError:
            continue
        except requests.exceptions.Timeout:
            return "Error: Request timed out"
        except Exception as e:
            return f"Error: {str(e)}"
    
    return "Error: Cannot connect to Ollama server."

async def validate_and_set_model(user_id, provider, model_name):
    test_messages = [{"role": "user", "content": "Test"}]
    response = await _make_ollama_request(model_name, test_messages)
    if response and not response.startswith("Error:"):
        model_manager.set_user_model(user_id, model_name)
        return True, f"Model '{model_name}' set."
    return False, f"Cannot use model '{model_name}'. {response}"

async def analyze_file(user_id: int, channel_id: int, filename: str, file_data: bytes, user_prompt: str = "", username: str = "", vision_mode: str = "concise", return_only_text: bool = False) -> str:
    """vision_mode: concise (short), examine (detailed), interrogate (very short). return_only_text: if True, return only extracted/response text (no header)."""
    date, time = update_system_time_date()
    
    # Get persona and model
    persona_name = persona_manager.get_user_persona(user_id)
    system_prompt = persona_manager.get_persona(persona_name)
    model_info = model_manager.get_user_model_info(user_id)
    model_name = model_info.get("model", "llama3.2:3b")
    
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
        model_used, response = await _try_models_with_fallback(model_name, messages, images=True)
        if response and response.startswith("⚠️"):
            pass  # keep warning message
        else:
            model_name = model_used
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
