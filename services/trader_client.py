"""HTTP client for the external Trader FastAPI service (authenticated)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import httpx

from integrations import TRADER_AUTH_TOKEN, TRADER_BASE_URL

AUTH_HEADER = "X-Trader-Auth-Token"
DEFAULT_TIMEOUT = 15.0


def _base() -> str:
    return (TRADER_BASE_URL or "").rstrip("/")


def _headers() -> Dict[str, str]:
    token = (TRADER_AUTH_TOKEN or "").strip()
    if not token:
        return {}
    return {AUTH_HEADER: token}


def trader_client_configured() -> bool:
    return bool(_base() and _headers())


async def _request(method: str, path: str, *, json_body: Any = None) -> Tuple[int, Any]:
    if not trader_client_configured():
        raise RuntimeError("Trader is not configured (set TRADER_BASE_URL and TRADER_AUTH_TOKEN).")
    url = f"{_base()}{path if path.startswith('/') else '/' + path}"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.request(method, url, headers=_headers(), json=json_body)
    text = (r.text or "").strip()
    try:
        data = r.json() if text else None
    except json.JSONDecodeError:
        data = text
    return r.status_code, data


async def fetch_trader_status_json() -> Tuple[int, Any]:
    """GET /status — returns HTTP status and parsed JSON (or raw string on parse failure)."""
    return await _request("GET", "/status")


async def post_trader_command(payload: Dict[str, Any]) -> Tuple[int, Any]:
    """POST /command — JSON body; same auth header."""
    return await _request("POST", "/command", json_body=payload)


async def _fetch_trader_status_raw_for_ai() -> Optional[str]:
    """Internal: dense JSON string from GET /status for LLM context (never user-facing)."""
    if not trader_client_configured():
        return None
    try:
        status_code, data = await fetch_trader_status_json()
    except Exception:
        return None
    if status_code >= 400:
        return json.dumps({"http_status": status_code, "body": data}, separators=(",", ":"), default=str)
    if isinstance(data, (dict, list)):
        return json.dumps(data, separators=(",", ":"), default=str)
    if data is None:
        return None
    return str(data)


async def append_trader_snapshot_to_llm_prompt(base: str, *, max_chars: int = 14_000) -> str:
    """If configured, append a compact /status JSON block for the model."""
    raw = await _fetch_trader_status_raw_for_ai()
    if not raw:
        return base
    block = f"\n\n[Internal trader snapshot — GET /status]\n{raw}"
    if len(block) > max_chars:
        block = block[: max_chars - 20] + "\n…(truncated)"
    return base + block
