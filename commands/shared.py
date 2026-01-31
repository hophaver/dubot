"""Shared helpers for commands (e.g. long message chunking)."""
import asyncio
import discord


async def send_long_message(interaction: discord.Interaction, message: str, max_length: int = 1900):
    if len(message) <= max_length:
        await interaction.followup.send(message)
        return
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
    for i, chunk in enumerate(chunks):
        await interaction.followup.send(chunk)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.5)
