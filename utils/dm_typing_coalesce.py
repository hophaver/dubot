"""
DM chat coalescing: wait while the user is typing, cancel in-flight generation when
typing starts or a new message arrives, and merge consecutive user texts into one LLM input.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import discord

log = logging.getLogger(__name__)

# Discord re-sends typing while the client holds the composer; treat quiet after this as "stopped typing".
TYPING_SETTLE_SECONDS = 2.2


@dataclass
class _ChannelState:
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    lines: List[str] = field(default_factory=list)
    epoch: int = 0
    typing_active: bool = False
    anchor: Optional[discord.Message] = None
    debounce_task: Optional[asyncio.Task] = None
    # Latest handler args (replaced on each user message so the loop always has current flags)
    handler_ctx: Optional[Dict[str, Any]] = None


class DmTypingCoalescer:
    """One background loop per DM channel; user messages and typing events bump coordination state."""

    def __init__(self) -> None:
        self._states: Dict[int, _ChannelState] = {}

    def _state(self, channel_id: int) -> _ChannelState:
        st = self._states.get(channel_id)
        if st is None:
            st = _ChannelState()
            self._states[channel_id] = st
        return st

    def _cancel_debounce(self, st: _ChannelState) -> None:
        t = st.debounce_task
        if t and not t.done():
            t.cancel()
        st.debounce_task = None

    async def _debounce_clear_typing(self, channel_id: int) -> None:
        try:
            await asyncio.sleep(TYPING_SETTLE_SECONDS)
            st = self._state(channel_id)
            async with st.cond:
                st.typing_active = False
                st.debounce_task = None
                st.cond.notify_all()
        except asyncio.CancelledError:
            pass

    async def note_user_typing(self, channel_id: int, user_id: int) -> None:
        """Discord on_typing — pause generation while the user may be composing."""
        st = self._state(channel_id)
        async with st.cond:
            if not st.typing_active:
                st.typing_active = True
                st.epoch += 1
            self._cancel_debounce(st)
            st.debounce_task = asyncio.create_task(
                self._debounce_clear_typing(channel_id),
                name=f"dm_typing_debounce_{channel_id}",
            )
            st.cond.notify_all()

    async def notify_user_message(
        self,
        channel_id: int,
        clean_content: str,
        anchor_message: discord.Message,
        handler_ctx: Dict[str, Any],
        start_loop: Callable[[int], Awaitable[None]],
    ) -> None:
        """Append text for this DM and wake / start the processor loop."""
        while True:
            st = self._state(channel_id)
            async with st.cond:
                if self._states.get(channel_id) is not st:
                    continue  # state was discarded as idle while we waited for the lock
                st.lines.append(clean_content)
                st.epoch += 1
                st.anchor = anchor_message
                st.handler_ctx = handler_ctx
                # A sent message supersedes "still typing" from the composer for this burst.
                st.typing_active = False
                self._cancel_debounce(st)
                st.cond.notify_all()
                break

        await start_loop(channel_id)

    async def prepend_lines_async(self, channel_id: int, prefix: List[str]) -> None:
        if not prefix:
            return
        st = self._state(channel_id)
        async with st.cond:
            st.lines = list(prefix) + list(st.lines)
            st.cond.notify_all()

    async def wait_batch(
        self, channel_id: int, idle_timeout: Optional[float] = None
    ) -> Optional[tuple[List[str], discord.Message, Dict[str, Any]]]:
        """Block until the user is not typing and there is at least one pending line; then dequeue all lines.

        Returns None if nothing arrived within ``idle_timeout`` seconds.
        """
        st = self._state(channel_id)
        async with st.cond:
            while st.typing_active or not st.lines:
                try:
                    await asyncio.wait_for(st.cond.wait(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    if st.typing_active or st.lines:
                        continue
                    return None
            batch = list(st.lines)
            st.lines.clear()
            anchor = st.anchor
            ctx = dict(st.handler_ctx or {})
            if anchor is None:
                raise RuntimeError("dm coalesce: anchor message missing")
            return batch, anchor, ctx

    async def discard_if_idle(self, channel_id: int) -> bool:
        """Forget this channel's state if nothing is pending. True if discarded."""
        st = self._states.get(channel_id)
        if st is None:
            return True
        async with st.cond:
            if st.lines or st.typing_active or self._states.get(channel_id) is not st:
                return False
            self._cancel_debounce(st)
            del self._states[channel_id]
            return True

    async def should_abort_generation(self, channel_id: int) -> bool:
        """True while the user is typing or new text is queued (another message arrived)."""
        st = self._state(channel_id)
        async with st.cond:
            return st.typing_active or bool(st.lines)

    async def pop_pending_lines(self, channel_id: int) -> List[str]:
        """Clear any lines still queued (e.g. superseded handler); returns what was removed."""
        st = self._state(channel_id)
        async with st.cond:
            out = list(st.lines)
            st.lines.clear()
            st.cond.notify_all()
            return out


dm_typing_coalescer = DmTypingCoalescer()
