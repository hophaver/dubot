from typing import Optional, List, Union
import discord
from whitelist import get_user_permission
from utils.llm_service import analyze_file, FileProcessor
from commands.shared import send_long_message

MAX_FILE_BYTES = 8 * 1024 * 1024


async def defer_and_read_file(
    interaction: discord.Interaction,
    file: discord.Attachment,
    allowed_types: Optional[Union[str, List[str]]] = None,
    type_error_msg: Optional[str] = None,
) -> Optional[bytes]:
    """Check permission, defer, read file, validate size and optional type. Returns file_data or None (sends errors)."""
    if not get_user_permission(interaction.user.id):
        await interaction.response.send_message("❌ Denied", ephemeral=True)
        return None
    await interaction.response.defer()
    try:
        file_data = await file.read()
    except Exception as e:
        await interaction.followup.send(f"❌ Error reading file: {str(e)[:150]}")
        return None
    if len(file_data) > MAX_FILE_BYTES:
        await interaction.followup.send("⚠️ File too large (max 8MB).")
        return None
    if allowed_types is not None:
        ft = FileProcessor.get_file_type(file.filename)
        ok = ft == allowed_types if isinstance(allowed_types, str) else ft in allowed_types
        if not ok:
            msg = type_error_msg or "⚠️ Wrong file type for this command."
            await interaction.followup.send(msg)
            return None
    return file_data


async def run_image_command(
    interaction: discord.Interaction,
    file: discord.Attachment,
    prompt: Optional[str],
    vision_mode: str,
    default_prompt: str,
):
    file_data = await defer_and_read_file(
        interaction, file,
        allowed_types="image",
        type_error_msg="⚠️ Please upload an image.",
    )
    if file_data is None:
        return
    try:
        result = await analyze_file(
            interaction.user.id, interaction.channel.id, file.filename, file_data,
            prompt or default_prompt, str(interaction.user.name), vision_mode=vision_mode,
        )
        await send_long_message(interaction, result)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
