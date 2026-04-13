"""Cursor Admin API helpers for team spend/balance style summaries."""

from typing import Any, Dict, Optional, Tuple

import requests

from integrations import CURSOR_API_KEY, CURSOR_USER_API_KEY

SPEND_URL = "https://api.cursor.com/teams/spend"


def fetch_spend_summary() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    POST /teams/spend. Returns (summary, None) on success.
    summary keys: on_demand_cents, overall_cents, included_cents, total_members, cycle_start_ms.
    On failure returns (None, user-facing error message).
    """
    cursor_key = CURSOR_USER_API_KEY or CURSOR_API_KEY
    if not cursor_key:
        return None, (
            "**CURSOR_USER_API_KEY** is not set. Add it to `.env` on the bot host.\n"
            "Fallback env name also supported: **CURSOR_API_KEY**.\n"
            "Note: spend endpoint access currently requires Cursor Admin API permissions: "
            "https://cursor.com/docs/api"
        )

    try:
        response = requests.post(
            SPEND_URL,
            auth=(cursor_key, ""),
            json={"page": 1, "pageSize": 1000},
            timeout=20,
        )
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"

    try:
        body = response.json()
    except ValueError:
        return None, f"Invalid JSON response (HTTP {response.status_code})."

    if response.status_code != 200:
        message = body.get("message") if isinstance(body, dict) else None
        if not message and isinstance(body, dict) and isinstance(body.get("error"), dict):
            message = body["error"].get("message")
        if not message:
            message = (response.text or f"HTTP {response.status_code}")[:500]
        if response.status_code == 403:
            message = (
                f"{message} (This endpoint requires Admin API access; "
                "user keys may not have this permission.)"
            )
        return None, f"Cursor API error ({response.status_code}): {message}"

    if not isinstance(body, dict):
        return None, "Unexpected response shape from Cursor API."

    members = body.get("teamMemberSpend")
    if not isinstance(members, list):
        return None, "Unexpected spend response shape from Cursor API."

    on_demand_cents = 0
    overall_cents = 0
    for member in members:
        if not isinstance(member, dict):
            continue
        try:
            on_demand_cents += int(member.get("spendCents", 0) or 0)
            overall_cents += int(member.get("overallSpendCents", 0) or 0)
        except (TypeError, ValueError):
            continue

    included_cents = max(overall_cents - on_demand_cents, 0)

    return {
        "on_demand_cents": on_demand_cents,
        "overall_cents": overall_cents,
        "included_cents": included_cents,
        "total_members": int(body.get("totalMembers", len(members)) or 0),
        "cycle_start_ms": body.get("subscriptionCycleStart"),
    }, None
