import os
import discord
from discord import app_commands
from whitelist import get_user_permission
from config import get_download_limit_mb
from commands.download._helpers import extract_urls, download_url_sync

def register(client: discord.Client):
    @client.tree.command(name="download", description="Download media from a link or the last message with media and send to chat")
    @app_commands.describe(link_or_message="Optional: URL to download, or leave empty to use last message with link/attachment")
    async def download(interaction: discord.Interaction, link_or_message: str = None):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        max_bytes = get_download_limit_mb() * 1024 * 1024
        channel = interaction.channel
        target_url = None
        target_attachment = None
        if link_or_message and link_or_message.strip():
            urls = extract_urls(link_or_message)
            if urls:
                target_url = urls[0]
        if not target_url:
            try:
                async for msg in channel.history(limit=20):
                    if msg.author.bot:
                        continue
                    urls = extract_urls(msg.content or "")
                    if urls:
                        target_url = urls[0]
                        break
                    for att in msg.attachments:
                        name = (att.filename or "").lower()
                        if any(name.endswith(ext) for ext in (".mp4", ".webm", ".mkv", ".mov", ".mp3", ".wav", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
                            target_attachment = att
                            break
                    if target_attachment:
                        break
            except Exception:
                pass
        if not target_url and not target_attachment:
            await interaction.followup.send("❌ No link or media found. Send a link, or use `/download` after a message that contains a link or attachment.")
            return
        data, filename = None, None
        if target_attachment:
            try:
                data = await target_attachment.read()
                filename = target_attachment.filename
                if len(data) > max_bytes:
                    await interaction.followup.send(f"❌ File too large. Max: {get_download_limit_mb()} MB.")
                    return
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to read attachment: {e}")
                return
        elif target_url:
            import asyncio
            data, filename = await asyncio.to_thread(download_url_sync, target_url, max_bytes)
            if data is None:
                await interaction.followup.send(f"❌ Download failed: {filename}")
                return
        if not data:
            await interaction.followup.send("❌ Nothing to send.")
            return
        from utils.llm_service import ask_llm
        ext = os.path.splitext(filename or "")[1].lower()
        is_image = ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        if is_image:
            try:
                reply = await ask_llm(
                    interaction.user.id, channel.id, "Describe or analyze this image.", str(interaction.user.name),
                    is_continuation=False, platform="discord", attachments=[{"filename": filename, "data": data}],
                )
                await interaction.followup.send(reply)
            except Exception as e:
                await interaction.followup.send(f"❌ Error analyzing image: {e}")
        else:
            try:
                from io import BytesIO
                await interaction.followup.send(f"📥 Downloaded: **{filename}**", file=discord.File(filename=filename, fp=BytesIO(data)))
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to send file: {e}")
