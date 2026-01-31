from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from conversations import conversation_manager
from commands.shared import send_long_message


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
        from utils.llm_service import ask_llm, analyze_file, compare_files, FileProcessor

        attachments = []
        for f in (file1, file2, file3):
            if f:
                try:
                    data = await f.read()
                    attachments.append({"filename": f.filename, "data": data, "content_type": f.content_type, "size": f.size})
                except Exception as e:
                    await interaction.followup.send(f"⚠️ Failed to read attachment: {str(e)[:100]}", ephemeral=True)

        msg_lower = message.lower()
        if len(attachments) >= 2 and any(k in msg_lower for k in ["compare", "difference", "similar", "contrast"]):
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

        if attachments and any(k in msg_lower for k in ["analyze", "review", "summarize", "extract", "read", "what's in", "describe this", "look at", "what is", "show me"]):
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

        answer = await ask_llm(
            interaction.user.id, interaction.channel.id, message, str(interaction.user.name),
            is_continuation=False, platform="discord", attachments=attachments if attachments else None,
        )
        await send_long_message(interaction, answer)
