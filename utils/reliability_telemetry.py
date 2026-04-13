"""Lightweight in-memory reliability telemetry counters."""
from __future__ import annotations

import threading
from typing import Dict

_LOCK = threading.Lock()
_COUNTERS: Dict[str, int] = {
    "llm_retries": 0,
    "llm_timeouts": 0,
    "llm_errors": 0,
    "discord_send_retries": 0,
    "discord_send_errors": 0,
    "message_handler_errors": 0,
}


def increment(event_name: str, value: int = 1) -> int:
    """Increase one counter and return its new value."""
    if value <= 0:
        return _COUNTERS.get(event_name, 0)
    with _LOCK:
        _COUNTERS[event_name] = _COUNTERS.get(event_name, 0) + value
        return _COUNTERS[event_name]


def snapshot() -> Dict[str, int]:
    """Return a copy of current counters."""
    with _LOCK:
        return dict(_COUNTERS)


def format_snapshot(prefix: str = "Reliability counters") -> str:
    data = snapshot()
    ordered = [
        f"llm_retries={data.get('llm_retries', 0)}",
        f"llm_timeouts={data.get('llm_timeouts', 0)}",
        f"llm_errors={data.get('llm_errors', 0)}",
        f"discord_send_retries={data.get('discord_send_retries', 0)}",
        f"discord_send_errors={data.get('discord_send_errors', 0)}",
        f"message_handler_errors={data.get('message_handler_errors', 0)}",
    ]
    return f"{prefix}: " + ", ".join(ordered)
