"""OpenRouter API helpers (credits require a management API key)."""

from typing import Any, Dict, Optional, Tuple

import requests

from integrations import OPENROUTER_API_KEY

CREDITS_URL = "https://openrouter.ai/api/v1/credits"


def fetch_credits() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    GET /api/v1/credits. Returns (payload, None) on success.
    payload keys: total_credits, total_usage, remaining (computed).
    On failure returns (None, user-facing error message).
    """
    if not OPENROUTER_API_KEY:
        return None, (
            "**OPENROUTER_API_KEY** is not set. Add it to `.env` on the bot host.\n"
            "Use an OpenRouter management API key for credits: "
            "https://openrouter.ai/docs/guides/overview/auth/management-api-keys"
        )

    try:
        r = requests.get(
            CREDITS_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            timeout=20,
        )
    except requests.RequestException as e:
        return None, f"Request failed: {e}"

    try:
        body = r.json()
    except ValueError:
        return None, f"Invalid JSON response (HTTP {r.status_code})."

    if r.status_code == 200:
        data = body.get("data") or {}
        try:
            total = float(data["total_credits"])
            used = float(data["total_usage"])
        except (KeyError, TypeError, ValueError):
            return None, "Unexpected credits response shape from OpenRouter."
        return {
            "total_credits": total,
            "total_usage": used,
            "remaining": total - used,
        }, None

    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    msg = err.get("message") if isinstance(err, dict) else None
    if not msg:
        msg = (r.text or f"HTTP {r.status_code}")[:500]
    return None, f"OpenRouter API error ({r.status_code}): {msg}"
