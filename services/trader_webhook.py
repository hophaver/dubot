"""Inbound HTTP webhook from the Trader service → Discord channel posts."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord

from integrations import TRADER_AUTH_TOKEN, TRADER_WEBHOOK_CHANNEL_ID
from config import get_startup_channel_id

if TYPE_CHECKING:
    from discord import Client

_log = logging.getLogger(__name__)


def _expected_token() -> str:
    return (TRADER_AUTH_TOKEN or "").strip()


def _verify_request_token(header_value: Optional[str]) -> bool:
    expected = _expected_token()
    if not expected:
        return False
    got = (header_value or "").strip()
    return got == expected


def _parse_event(body: Dict[str, Any]) -> tuple[str, str]:
    """Return (kind, summary) where kind is buy|sell|error|info."""
    ev = (
        body.get("event")
        or body.get("type")
        or body.get("action")
        or body.get("side")
        or "info"
    )
    ev_s = str(ev).strip().lower()
    if ev_s in ("b", "buy", "long", "purchase"):
        ev_s = "buy"
    elif ev_s in ("s", "sell", "short", "close", "exit"):
        ev_s = "sell"
    elif ev_s in ("err", "error", "exception", "fail", "failure"):
        ev_s = "error"

    parts = []
    for key in ("symbol", "ticker", "pair", "instrument"):
        v = body.get(key)
        if v:
            parts.append(f"{key}={v}")
    for key in ("message", "detail", "reason", "error", "text"):
        v = body.get(key)
        if v and str(v) not in parts:
            parts.append(str(v)[:500])
            break
    if not parts:
        parts.append(json.dumps(body, default=str)[:900])
    summary = " · ".join(parts)
    return ev_s, summary


def _embed_for_event(kind: str, summary: str) -> discord.Embed:
    if kind == "buy":
        color = discord.Color.green()
        title = "Trader · Buy"
    elif kind == "sell":
        color = discord.Color.orange()
        title = "Trader · Sell"
    elif kind == "error":
        color = discord.Color.red()
        title = "Trader · Error"
    else:
        color = discord.Color.dark_teal()
        title = "Trader · Update"
    emb = discord.Embed(title=title, description=summary[:4000], color=color)
    return emb


async def _resolve_webhook_channel(client: Client) -> Optional[discord.abc.Messageable]:
    cid = TRADER_WEBHOOK_CHANNEL_ID or get_startup_channel_id()
    if not cid:
        return None
    ch = client.get_channel(int(cid))
    if ch is not None and isinstance(ch, discord.abc.Messageable):
        return ch
    try:
        fetched = await client.fetch_channel(int(cid))
        if isinstance(fetched, discord.abc.Messageable):
            return fetched
    except Exception as e:
        _log.warning("trader webhook: could not fetch channel %s: %s", cid, e)
    return None


class TraderWebhookServer:
    """Minimal aiohttp app: POST /trader/webhook with X-Trader-Auth-Token."""

    def __init__(self, client: Client, port: int):
        self.client = client
        self.port = int(port)
        self._runner = None
        self._site = None

    async def start(self) -> None:
        if self.port <= 0:
            return
        try:
            from aiohttp import web
        except ImportError:
            _log.warning("aiohttp not installed; trader webhook listener disabled")
            return

        async def handle(request: "web.Request") -> "web.StreamResponse":
            if request.method != "POST":
                return web.Response(status=405, text="Method Not Allowed")
            token = request.headers.get("X-Trader-Auth-Token") or request.headers.get("Authorization", "").replace(
                "Bearer ", "", 1
            ).strip()
            if not _verify_request_token(token):
                return web.Response(status=401, text="Unauthorized")
            try:
                body = await request.json()
            except Exception:
                return web.Response(status=400, text="Invalid JSON")
            if not isinstance(body, dict):
                return web.Response(status=400, text="JSON object required")

            async def _post():
                channel = await _resolve_webhook_channel(self.client)
                if not channel:
                    _log.warning("trader webhook: no TRADER_WEBHOOK_CHANNEL_ID or startup channel")
                    return
                kind, summary = _parse_event(body)
                embed = _embed_for_event(kind, summary)
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    _log.exception("trader webhook: failed to post: %s", e)

            asyncio.create_task(_post())
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_post("/trader/webhook", handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="0.0.0.0", port=self.port)
        await self._site.start()
        print(f"Trader webhook: listening on 0.0.0.0:{self.port} POST /trader/webhook", flush=True)

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
