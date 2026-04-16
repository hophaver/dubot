"""Shared helpers for commands (e.g. long message chunking)."""
import asyncio
import re
from typing import Optional

import discord

MAX_MESSAGE_LENGTH = 1900

# Discord does not render LaTeX; $...$ shows as broken raw text.
_DISCORD_MATH_BLOCK = re.compile(r"\$\$([\s\S]*?)\$\$")
_DISCORD_MATH_INLINE = re.compile(r"\$([^$\n]{1,800}?)\$")
_DISCORD_PARENS_MATH = re.compile(r"\\\(([^)]{0,800}?)\\\)")
_DISCORD_BRACKET_MATH = re.compile(r"\\\[([\s\S]*?)\\\]")


def _latex_to_plain_fragment(s: str) -> str:
    """Turn a small LaTeX fragment into Discord-safe plain text (Unicode symbols)."""
    if not s:
        return ""
    t = s.strip()
    for _ in range(24):
        n = re.sub(r"\\text(?:rm|sf|bf|it)?\{([^}]*)\}", r"\1", t)
        n = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", n)
        if n == t:
            break
        t = n
    t = t.replace(r"\times", "×")
    t = t.replace(r"\cdot", "·")
    t = t.replace(r"\approx", "≈")
    t = t.replace(r"\pm", "±")
    t = t.replace(r"\leq", "≤")
    t = t.replace(r"\geq", "≥")
    t = t.replace(r"\neq", "≠")
    t = t.replace(r"\deg", "°")
    t = t.replace(r"\mu", "µ")
    t = t.replace(r"\Omega", "Ω")
    t = t.replace(r"\,", " ")
    t = t.replace(r"\%", "%")
    t = t.replace("--", "–")
    t = re.sub(r"\\[a-zA-Z]+\*?", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _maybe_convert_inline_dollar_math(inner: str) -> str:
    """If $...$ looks like LaTeX, convert; otherwise keep (e.g. simple $5$ prices)."""
    inner_s = inner.strip()
    if not inner_s:
        return ""
    if "\\" in inner_s or re.search(r"\\[a-zA-Z]+\{", inner_s) or "{" in inner_s:
        return _latex_to_plain_fragment(inner_s)
    if re.fullmatch(r"\d+([.,]\d+)?", inner_s):
        return f"${inner_s}$"
    return f"${inner_s}$"


def sanitize_discord_bot_content(text: str) -> str:
    """
    Strip/repair LaTeX-style math for Discord: the client does not render $...$ or \\(...\\).
    Uses Unicode (×, ≈, …) instead. Safe to run on all bot-authored Discord text.
    """
    if not text:
        return text
    if "$" not in text and "\\[" not in text and "\\(" not in text:
        return text

    out = text
    out = _DISCORD_BRACKET_MATH.sub(lambda m: _latex_to_plain_fragment(m.group(1)), out)
    out = _DISCORD_MATH_BLOCK.sub(lambda m: _latex_to_plain_fragment(m.group(1)), out)
    out = _DISCORD_PARENS_MATH.sub(lambda m: _latex_to_plain_fragment(m.group(1)), out)

    def _sub_inline(m: re.Match) -> str:
        return _maybe_convert_inline_dollar_math(m.group(1))

    for _ in range(32):
        nxt = _DISCORD_MATH_INLINE.sub(_sub_inline, out)
        if nxt == out:
            break
        out = nxt
    return out


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
    message = sanitize_discord_bot_content(message)
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
        await interaction.followup.send(sanitize_discord_bot_content(message))
        return
    chunks = _chunk_message(message, max_length)
    for i, chunk in enumerate(chunks):
        await interaction.followup.send(chunk)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.5)
