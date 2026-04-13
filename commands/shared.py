"""Shared helpers for commands (e.g. long message chunking)."""
import asyncio
from typing import Optional

import discord

MAX_MESSAGE_LENGTH = 1900


def bot_embed_thumbnail_url(bot_user: Optional[discord.ClientUser]) -> Optional[str]:
    """Avatar URL safe for embed thumbnails.

    Discord rejects many default-avatar CDN URLs in embeds with 404 *asset not found*;
    only return a URL when the bot has a custom uploaded avatar.
    """
    if bot_user is None:
        return None
    if bot_user.avatar is None:
        return None
    return str(bot_user.display_avatar.url)


def _chunk_message(message: str, max_length: int = MAX_MESSAGE_LENGTH):
    """Split message into chunks at paragraph/line boundaries, each <= max_length."""
    if len(message) <= max_length:
        return [message] if message else []
    chunks = []
    current = ""
    for part in message.split("\n\n"):
        if len(current) + len(part) + 2 <= max_length:
            current = f"{current}\n\n{part}".lstrip() if current else part
        else:
            if current:
                chunks.append(current)
            if len(part) <= max_length:
                current = part
            else:
                for line in part.split("\n"):
                    if len(line) > max_length:
                        if current:
                            chunks.append(current)
                            current = ""
                        while line:
                            chunks.append(line[:max_length])
                            line = line[max_length:]
                    elif len(current) + len(line) + 1 <= max_length:
                        current = f"{current}\n{line}".lstrip() if current else line
                    else:
                        if current:
                            chunks.append(current)
                        current = line
    if current:
        chunks.append(current)
    return chunks


async def send_long_to_channel(channel, message: str, max_length: int = MAX_MESSAGE_LENGTH):
    """Send a possibly long message to a channel in chunks."""
    chunks = _chunk_message(message, max_length)
    for i, chunk in enumerate(chunks):
        await channel.send(chunk)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.5)


async def send_long_message(interaction: discord.Interaction, message: str, max_length: int = MAX_MESSAGE_LENGTH):
    if len(message) <= max_length:
        await interaction.followup.send(message)
        return
    chunks = _chunk_message(message, max_length)
    for i, chunk in enumerate(chunks):
        await interaction.followup.send(chunk)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.5)
