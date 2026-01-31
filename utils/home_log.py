"""Send logs and errors to the /sethome channel. Set client in on_ready."""
import asyncio
from typing import Optional

_client: Optional["discord.Client"] = None  # type: ignore[name-defined]


def set_client(client) -> None:
    """Call from main on_ready so home channel sends work."""
    global _client
    _client = client


def _get_channel():
    """Return the home channel or None."""
    if _client is None:
        return None
    from config import get_startup_channel_id
    channel_id = get_startup_channel_id()
    if channel_id is None:
        return None
    return _client.get_channel(channel_id)


async def send_to_home(
    content: Optional[str] = None,
    embed: Optional["discord.Embed"] = None,  # type: ignore[name-defined]
    view: Optional["discord.ui.View"] = None,  # type: ignore[name-defined]
) -> bool:
    """Send a message to the home channel (content, embed, view). Returns True if sent."""
    channel = _get_channel()
    if channel is None:
        return False
    if not content and not embed and not view:
        return False
    try:
        kwargs = {}
        if content:
            kwargs["content"] = content[:2000]
        if embed:
            kwargs["embed"] = embed
        if view:
            kwargs["view"] = view
        await channel.send(**kwargs)
        return True
    except Exception:
        return False


async def log(message: str, *, also_send: bool = True) -> None:
    """Print to console and optionally send to home channel."""
    print(message)
    if also_send and _client:
        await send_to_home(message)


def log_sync(message: str) -> None:
    """Print to console and schedule send to home (for use from sync/thread code)."""
    print(message)
    if _client and _client.loop and _client.is_ready():
        try:
            asyncio.run_coroutine_threadsafe(
                send_to_home(message[:2000]),
                _client.loop,
            )
        except Exception:
            pass
