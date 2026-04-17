"""Track background DM tasks (history summarization, profile refresh) so shutdown can wait."""

from __future__ import annotations

import asyncio
from typing import Coroutine, Set, TypeVar

T = TypeVar("T")

_tasks: Set[asyncio.Task] = set()


def spawn(coro: Coroutine[None, None, T], *, name: str | None = None) -> asyncio.Task[T]:
    """Fire-and-forget background work; tracked for graceful shutdown."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)

    task.add_done_callback(_done)
    return task


async def wait_all(timeout: float | None = None) -> None:
    """Await all tracked background tasks (used on bot shutdown)."""
    pending = [t for t in _tasks if not t.done()]
    if not pending:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)
    except asyncio.TimeoutError:
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
