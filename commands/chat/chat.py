import asyncio
from typing import Optional, List, Dict, Any
import discord
from discord import app_commands
from whitelist import get_user_permission
from conversations import conversation_manager
from commands.shared import send_long_message


COMPARE_KEYWORDS = {"compare", "difference", "similar", "contrast"}
ANALYZE_KEYWORDS = {
    "analyze",
    "review",
    "summarize",
    "extract",
    "read",
    "what's in",
    "describe this",
    "look at",
    "what is",
    "show me",
}


def _is_dm_channel(channel: Optional[discord.abc.Messageable]) -> bool:
    return isinstance(channel, discord.DMChannel)


async def _read_attachments(files: List[Optional[discord.Attachment]], interaction: discord.Interaction) -> List[Dict[str, Any]]:
    attachments: List[Dict[str, Any]] = []
    for attachment in files:
        if not attachment:
            continue
        try:
            data = await attachment.read()
            attachments.append(
                {
                    "filename": attachment.filename,
                    "data": data,
                    "content_type": attachment.content_type,
                    "size": attachment.size,
                }
            )
        except Exception as exc:
            await interaction.followup.send(
                f"⚠️ Failed to read attachment: {str(exc)[:100]}",
                ephemeral=True,
            )
    return attachments


def _schedule_adaptive_profile_update(interaction: discord.Interaction, message: str) -> None:
    if not message or not interaction:
        return
    user_id = interaction.user.id
    try:
        from adaptive_dm import adaptive_dm_manager

        if not adaptive_dm_manager.is_enabled(user_id):
            return

        async def _runner():
            await asyncio.to_thread(adaptive_dm_manager.apply_live_message_tune, user_id, message)
            await asyncio.to_thread(adaptive_dm_manager.run_tone_tuning_now, user_id, False)

        asyncio.create_task(_runner())
    except Exception:
        pass


def register(client: discord.Client):
    @client.tree.command(name="chat", description="Chat with AI (supports file attachments)")
    @app_commands.describe(
        message="Your message to the AI",
        file1="Optional file attachment",
        file2="Optional second file",
        file3="Optional third file",
    )
    async def chat(
        interaction: discord.Interaction,
        message: str,
        file1: Optional[discord.Attachment] = None,
        file2: Optional[discord.Attachment] = None,
        file3: Optional[discord.Attachment] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        from utils.llm_service import ask_llm, analyze_file, compare_files

        attachments = await _read_attachments([file1, file2, file3], interaction)

        msg_lower = message.lower()
        is_dm = _is_dm_channel(interaction.channel)
        if is_dm:
            from adaptive_dm import adaptive_dm_manager as _adm

            _label = (getattr(interaction.user, "global_name", None) or interaction.user.name or "").strip()
            _adm.touch_adaptive_sync_display_name(interaction.user.id, _label)
        fast_reply_enabled = (not is_dm) or conversation_manager.is_dm_fast_reply_active(interaction.channel.id)

        if len(attachments) >= 2 and any(keyword in msg_lower for keyword in COMPARE_KEYWORDS):
            try:
                file_data_list = [{"filename": a["filename"], "data": a["data"]} for a in attachments]
                result = await compare_files(
                    interaction.user.id, interaction.channel.id, file_data_list, message, str(interaction.user.name)
                )
                await send_long_message(interaction, result)
                conversation_manager.add_message(
                    interaction.channel.id, "user",
                    f"{interaction.user.name} says: [Compare files] {message}"
                )
                conversation_manager.add_message(interaction.channel.id, "assistant", result)
                conversation_manager.save()
                return
            except Exception as e:
                await interaction.followup.send(f"⚠️ Error comparing files: {str(e)[:100]}")

        if attachments and any(keyword in msg_lower for keyword in ANALYZE_KEYWORDS):
            try:
                a = attachments[0]
                result = await analyze_file(
                    interaction.user.id, interaction.channel.id, a["filename"], a["data"],
                    message, str(interaction.user.name)
                )
                await send_long_message(interaction, result)
                conversation_manager.add_message(interaction.channel.id, "user", f"{interaction.user.name} says: [File: {a['filename']}] {message}")
                conversation_manager.add_message(interaction.channel.id, "assistant", result)
                conversation_manager.save()
                return
            except Exception as e:
                await interaction.followup.send(f"⚠️ Error analyzing file: {str(e)[:100]}")

        try:
            answer = await asyncio.wait_for(
                ask_llm(
                    interaction.user.id,
                    interaction.channel.id,
                    message,
                    str(interaction.user.name),
                    is_continuation=False,
                    platform="discord",
                    attachments=attachments if attachments else None,
                    is_dm=is_dm,
                    fast_reply=fast_reply_enabled,
                ),
                timeout=150,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("⚠️ I timed out while generating a reply. Please try again.")
            return
        except Exception as exc:
            await interaction.followup.send(f"⚠️ I hit an internal error: {str(exc)[:140]}")
            return

        await send_long_message(interaction, answer)
        if is_dm:
            _schedule_adaptive_profile_update(interaction, message)
