"""File attachments: type detection, text extraction, single-file analysis and multi-file comparison."""
import base64
import mimetypes
import os
from typing import Dict, List

from adaptive_dm import is_adaptive_context_export_filename
from conversations import conversation_manager
from integrations import update_system_time_date
from utils.llm import providers
from utils.llm.fallback import try_models_with_fallback
from utils.llm.prompts import function_model, function_system, get_enhanced_prompt
from utils.llm.text import clean_response

# File support configuration
SUPPORTED_FILE_TYPES = {
    'image': ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'],
    'text': ['.txt', '.json', '.csv', '.xml', '.yaml', '.yml', '.md', '.log'],
    'code': ['.py', '.js', '.html', '.css', '.java', '.cpp', '.c', '.go', '.rs', '.php', '.sh'],
    'document': ['.pdf'],
}


SUPPORTED_EXTENSION_TO_TYPE = {
    ext: file_type
    for file_type, extensions in SUPPORTED_FILE_TYPES.items()
    for ext in extensions
}


class FileProcessor:
    """Process and analyze different types of files"""

    @staticmethod
    def get_file_type(filename: str) -> str:
        """Get the type of file based on extension"""
        ext = os.path.splitext((filename or "").lower())[1]
        return SUPPORTED_EXTENSION_TO_TYPE.get(ext, "unknown")

    @staticmethod
    def read_text_file(content: bytes) -> str:
        """Decode text bytes (UTF-8, then Latin-1)."""
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1")

    @staticmethod
    def extract_text(filename: str, content: bytes) -> str:
        """Plain text for text/code files and PDFs (via pypdf)."""
        if (filename or "").lower().endswith(".pdf"):
            import io
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            text = "\n\n".join(p for p in pages if p)
            return text or "[No extractable text in this PDF — it may be scanned images.]"
        return FileProcessor.read_text_file(content)

    @staticmethod
    def prepare_image_for_llm(image_data: bytes, filename: str) -> Dict:
        """Prepare image data for LLM with vision capabilities"""
        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        return {
            "data": base64.b64encode(image_data).decode("utf-8"),
            "mime_type": mime_type,
            "filename": filename,
        }

    @staticmethod
    def default_analysis_prompt(filename: str, file_type: str) -> str:
        """Instruction used when the user gave no prompt (file content is appended separately)."""
        if file_type == "text":
            return f"Analyze the text file '{filename}'. Provide: 1) Summary, 2) Key points, 3) Any issues or suggestions"
        if file_type == "code":
            return (
                f"Review the code file '{filename}'. Provide: 1) What the code does, 2) Potential bugs or issues, "
                "3) Suggestions for improvement, 4) Security concerns if any"
            )
        if file_type == "image":
            return (
                f"Analyze this image file: '{filename}'. Describe what you see in detail, including any text, "
                "objects, colors, and context. If there's text in the image, transcribe it."
            )
        if file_type == "document":
            return f"Analyze the document '{filename}'. Provide: 1) Document type, 2) Main topics, 3) Key information, 4) Summary"
        return f"Analyze this file '{filename}'. Provide information about what type of file it appears to be and any observations."


async def analyze_file(
    user_id: int,
    channel_id: int,
    filename: str,
    file_data: bytes,
    user_prompt: str = "",
    username: str = "",
    vision_mode: str = "concise",
    return_only_text: bool = False,
    *,
    record_in_conversation: bool = True,
) -> str:
    """vision_mode: concise (short), examine (detailed), interrogate (very short). return_only_text: if True, return only extracted/response text (no header)."""
    if is_adaptive_context_export_filename(filename):
        return (
            "That file is reserved for **`/adaptive-status`** replies in DMs. "
            "Reply to the status message with **`adaptive-dm-context.txt`** attached to replace your adaptive context — "
            "do not use **`/analyze`** for it."
        )
    today, now_hm = update_system_time_date()

    # Persona and model come from per-function prefs (file analysis is never adaptive)
    model_name, provider = function_model(user_id, "file_analysis")
    
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
            analysis_prompt = FileProcessor.default_analysis_prompt(filename, file_type)
    
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
        enhanced_system_prompt = function_system("file_analysis", get_enhanced_prompt(key))
    else:
        enhanced_system_prompt = function_system(
            "file_analysis",
            get_enhanced_prompt("file_analysis_other", date=today, time=now_hm, filename=filename, file_type=file_type),
        )
    
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
                text_content = FileProcessor.extract_text(filename, file_data)
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
        model_used, response = await try_models_with_fallback(model_name, messages, images=True, provider=provider)
        if response and response.startswith("⚠️"):
            pass  # keep warning message
        else:
            model_name = model_used
    else:
        response = await providers.chat(provider, model_name, messages)
        if response is None:
            response = ""
    
    # Clean and format response
    response = clean_response(response or "")
    
    if record_in_conversation:
        conversation_manager.add_message(
            channel_id,
            "user",
            f"{username} says: [File Upload: {filename}] {user_prompt or 'Analyze this file'}",
        )
        conversation_manager.add_message(channel_id, "assistant", response)
        conversation_manager.save()

    if return_only_text:
        return response
    final_response = f"📄 **File Analysis: {filename}**\n"
    final_response += f"*Type: {file_type.upper()} | Size: {len(file_data):,} bytes*\n\n"
    final_response += response
    return final_response


async def compare_files(
    user_id: int,
    channel_id: int,
    files: List[Dict],
    user_prompt: str = "",
    username: str = "",
    *,
    record_in_conversation: bool = True,
) -> str:
    """Compare multiple text files"""
    today, now_hm = update_system_time_date()
    model_name, provider = function_model(user_id, "compare_files")
    
    # Extract text from all files
    file_contents = []
    for file_info in files:
        try:
            text_content = FileProcessor.extract_text(file_info['filename'], file_info['data'])
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
    
    enhanced_system_prompt = function_system("compare_files", get_enhanced_prompt("compare_files", date=today, time=now_hm))
    
    messages = [
        {"role": "system", "content": enhanced_system_prompt},
        {"role": "user", "content": f"{analysis_prompt}\n\nFiles to compare:{file_content_str}"}
    ]
    
    response = await providers.chat(provider, model_name, messages)
    response = clean_response(response or "")
    
    # Format response
    header = f"🔍 **File Comparison: {len(files)} files**\n"
    for file_info in files:
        header += f"- {file_info['filename']} ({len(file_info['data']):,} bytes)\n"
    
    final_response = header + "\n" + response
    
    if record_in_conversation:
        file_names = ", ".join([f["filename"] for f in files])
        conversation_manager.add_message(
            channel_id,
            "user",
            f"{username} says: [File Comparison: {file_names}] {user_prompt or 'Compare these files'}",
        )
        conversation_manager.add_message(channel_id, "assistant", response)
        conversation_manager.save()

    return final_response
