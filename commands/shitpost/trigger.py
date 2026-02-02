"""Shitpost trigger: !word / .word (single token, 3+ letters). .age/!age -> random 1-18 or 30-70; else LLM (max 2 words)."""
import random
from config import get_wake_word
from utils.llm_service import ask_llm_shitpost
from . import _blacklist


def _parse_shitpost(content: str) -> str | None:
    """Return word (lowercase) if content matches shitpost pattern, else None."""
    content = (content or "").strip()
    if len(content) < 4:
        return None
    first = content[0]
    if first not in "!.":
        return None
    rest = content[1:]
    if " " in rest or len(rest) < 3:
        return None
    if not rest.isalpha():
        return None
    word = rest.lower()
    if word == get_wake_word().lower():
        return None
    if word in _blacklist.get_ignored_words():
        return None
    return word


async def handle_shitpost(client, message) -> bool:
    """Handle shitpost messages; return True if handled."""
    word = _parse_shitpost(message.content)
    if word is None:
        return False

    if word == "age":
        n = random.choice([random.randint(1, 18), random.randint(30, 70)])
        await message.channel.send(str(n))
        return True

    async with message.channel.typing():
        reply = await ask_llm_shitpost(message.author.id, word)
    if reply:
        await message.channel.send(reply)
    else:
        await message.channel.send("…")
    return True
