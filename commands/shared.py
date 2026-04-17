"""Shared helpers for commands (e.g. long message chunking)."""
import asyncio
import re
from typing import List, Optional, Tuple

import discord

MAX_MESSAGE_LENGTH = 1900

# Discord fenced code: opening line ``` or ```lang ; closing line ```
_FENCE_OPEN_RE = re.compile(r"^\s*```([a-zA-Z0-9_+\-#]*)\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")

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


def _fence_opener_line(lang: str) -> str:
    lang = (lang or "").strip()
    return f"```{lang}" if lang else "```"


def _parse_fenced_segments(text: str) -> List[Tuple[str, ...]]:
    """
    Split text into segments: ("text", str) or ("code", opener_line, body_str).
    opener_line is e.g. ```python ; body_str is inner content without outer fences.
    Unclosed fences (no trailing ```) still yield one code segment; emit layer always closes.
    """
    lines = text.split("\n")
    out: List[Tuple[str, ...]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m_open = _FENCE_OPEN_RE.match(line)
        if m_open:
            lang = m_open.group(1) or ""
            opener = _fence_opener_line(lang)
            i += 1
            body_lines: List[str] = []
            while i < n and not _FENCE_CLOSE_RE.match(lines[i]):
                body_lines.append(lines[i])
                i += 1
            if i < n and _FENCE_CLOSE_RE.match(lines[i]):
                i += 1
            body = "\n".join(body_lines)
            out.append(("code", opener, body))
            continue
        start = i
        while i < n and not _FENCE_OPEN_RE.match(lines[i]):
            i += 1
        block = "\n".join(lines[start:i])
        if block:
            out.append(("text", block))
    return out


def _emit_code_chunks(opener: str, body: str, max_length: int) -> List[str]:
    """Emit one or more strings; each is a complete fenced block (same opener, closed with ```)."""
    inner = body or ""
    prefix = f"{opener}\n"
    suffix = "\n```"
    one = f"{prefix}{inner}{suffix}"
    if len(one) <= max_length:
        return [one]

    overhead = len(prefix) + len(suffix)
    room = max_length - overhead
    if room < 1:
        room = 1

    chunks: List[str] = []
    pos = 0
    n = len(inner)
    while pos < n:
        take = min(room, n - pos)
        piece = inner[pos : pos + take]
        pos += take
        chunks.append(f"{prefix}{piece}{suffix}")
    if not chunks:
        chunks.append(f"{prefix}{suffix}")
    return chunks


def _chunk_plain_lines(text: str, max_length: int) -> List[str]:
    """Greedy line-based chunking for non-fence text (paragraph aware)."""
    if not text:
        return []
    if len(text) <= max_length:
        return [text]
    chunks: List[str] = []
    current = ""
    for part in text.split("\n\n"):
        if len(current) + len(part) + (2 if current else 0) <= max_length:
            current = f"{current}\n\n{part}" if current else part
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
                        start = 0
                        while start < len(line):
                            chunks.append(line[start : start + max_length])
                            start += max_length
                        current = ""
                    elif len(current) + len(line) + (1 if current else 0) <= max_length:
                        current = f"{current}\n{line}" if current else line
                    else:
                        if current:
                            chunks.append(current)
                        current = line
    if current:
        chunks.append(current)
    return chunks


def _merge_piece_strings(pieces: List[str], max_length: int) -> List[str]:
    """Greedy-merge adjacent pieces into Discord messages <= max_length."""
    out: List[str] = []
    buf = ""
    for p in pieces:
        if not p:
            continue
        if not buf:
            buf = p
            continue
        if len(buf) + 1 + len(p) <= max_length:
            buf = f"{buf}\n{p}"
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def _chunk_message(message: str, max_length: int = MAX_MESSAGE_LENGTH):
    """Split for Discord: each part <= max_length; fenced ``` blocks are closed before limit then continued."""
    message = sanitize_discord_bot_content(message)
    if not message:
        return []
    if len(message) <= max_length:
        return [message]

    segments = _parse_fenced_segments(message)
    if not segments:
        return _chunk_plain_lines(message, max_length)

    pieces: List[str] = []
    for seg in segments:
        if seg[0] == "text":
            pieces.extend(_chunk_plain_lines(str(seg[1]), max_length))
        elif seg[0] == "code":
            pieces.extend(_emit_code_chunks(str(seg[1]), str(seg[2]), max_length))
    return _merge_piece_strings(pieces, max_length)


_CHUNK_SEND_DELAY = 0.05


async def send_long_to_channel(channel, message: str, max_length: int = MAX_MESSAGE_LENGTH):
    """Send a possibly long message to a channel in chunks."""
    chunks = _chunk_message(message, max_length)
    for i, chunk in enumerate(chunks):
        await channel.send(chunk)
        if i < len(chunks) - 1:
            await asyncio.sleep(_CHUNK_SEND_DELAY)


async def send_long_message(interaction: discord.Interaction, message: str, max_length: int = MAX_MESSAGE_LENGTH):
    if len(message) <= max_length:
        await interaction.followup.send(sanitize_discord_bot_content(message))
        return
    chunks = _chunk_message(message, max_length)
    for i, chunk in enumerate(chunks):
        await interaction.followup.send(chunk)
        if i < len(chunks) - 1:
            await asyncio.sleep(_CHUNK_SEND_DELAY)
