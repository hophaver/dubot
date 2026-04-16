"""Shared Discord embed: bot operational overview (startup home message and /status)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

import discord

from commands.shared import bot_embed_thumbnail_url
from config import get_current_persona, get_startup_channel_id, get_wake_word
from integrations import PERMANENT_ADMIN
from models import model_manager
from services.status_server import PORT as STATUS_PORT
from utils.openrouter import fetch_credits
from utils.update_state import update_state_manager


def _fmt_credit_amount(n: float) -> str:
    if abs(n - round(n)) < 1e-6:
        return f"{int(round(n)):,}"
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def _discord_relative(ts: int) -> str:
    if not ts or ts <= 0:
        return "—"
    return f"<t:{int(ts)}:R>"


async def build_bot_overview_embed(
    client: discord.Client,
    *,
    errors: Optional[List[str]] = None,
    include_hardware: bool = False,
    sys_status: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], discord.Embed]:
    """Return (errors_list, embed). If errors is None, read from client._startup_errors (startup only)."""
    if errors is None:
        errors = list(getattr(client, "_startup_errors", []) or [])
    else:
        errors = list(errors)

    boot_ts = int(time.time())

    from integrations import (
        HA_URL,
        HA_ACCESS_TOKEN,
        LOCATION,
        OLLAMA_URL,
        OPENROUTER_CHAT_API_KEY,
        OPENROUTER_MANAGEMENT_API_KEY,
    )

    model_owner_id = PERMANENT_ADMIN
    model_info = model_manager.get_user_model_info(model_owner_id)
    chat_provider = str(model_info.get("provider", "local")).strip().lower() or "local"
    chat_model = str(model_info.get("model", "qwen2.5:7b")).strip() or "qwen2.5:7b"
    basic_local_model = model_manager.get_last_local_model(model_owner_id, refresh_local=True)

    news_provider = "local"
    news_model = None
    try:
        from services.news_service import get_news_model

        news_provider, news_model = get_news_model()
    except Exception:
        pass
    if news_model:
        news_line = f"`{news_model}` ({news_provider})"
    else:
        news_line = f"same as chat (`{chat_model}`)"

    local_models = model_manager.list_all_models(refresh_local=False)
    n_local = len(local_models)

    ha_line = "Not configured"
    try:
        if HA_URL and str(HA_URL).strip() and HA_ACCESS_TOKEN:
            from utils.ha_integration import ha_manager

            entities = await ha_manager.get_all_entities()
            ha_line = "Connected" if entities else "Unreachable"
        elif not HA_ACCESS_TOKEN:
            ha_line = "Not configured"
    except Exception:
        ha_line = "Unreachable"

    try:
        loc = LOCATION if (LOCATION and str(LOCATION) != "Unknown") else "Unknown"
    except Exception:
        loc = "Unknown"

    wake = get_wake_word()
    home_line = "Set" if get_startup_channel_id() else "Not set"

    state = update_state_manager.get_state()
    last_update_at = int(state.get("last_update_at") or 0)
    updated_rel = _discord_relative(last_update_at) if last_update_at > 0 else "never (no `/update` yet)"

    if OPENROUTER_MANAGEMENT_API_KEY or OPENROUTER_CHAT_API_KEY:
        data, err = await asyncio.to_thread(fetch_credits)
        if data:
            total = float(data["total_credits"])
            rem = float(data["remaining"])
            credits_line = f"Credits **{_fmt_credit_amount(rem)}** / **{_fmt_credit_amount(total)}**"
        else:
            credits_line = f"Unavailable ({(err or 'error')[:100]})"
    else:
        credits_line = "Not configured (no API key)"

    key_parts = []
    if OPENROUTER_CHAT_API_KEY:
        key_parts.append("chat")
    if OPENROUTER_MANAGEMENT_API_KEY:
        key_parts.append("management")
    keys_line = ", ".join(key_parts) if key_parts else "none"

    openrouter_value = f"{credits_line}\nConfigured keys: {keys_line}"

    ollama_line = f"{OLLAMA_URL} · {n_local} local model(s)" if n_local else f"{OLLAMA_URL} · no local models"

    cmd_line = "All packages loaded" if not errors else ", ".join(errors)[:900]

    desc_lines = [
        f"**Updated** {updated_rel}",
        f"**Last boot** {_discord_relative(boot_ts)}",
        f"**Admin** <@{PERMANENT_ADMIN}>",
    ]
    if errors:
        desc_lines.insert(0, "Some command packages did not load.")

    embed = discord.Embed(
        title="Online" if not errors else "Online with warnings",
        description="\n".join(desc_lines),
        color=discord.Color.green() if not errors else discord.Color.orange(),
    )
    _thumb = bot_embed_thumbnail_url(client.user)
    if _thumb:
        embed.set_thumbnail(url=_thumb)

    embed.add_field(name="OpenRouter", value=openrouter_value[:1024], inline=False)
    embed.add_field(
        name="Models",
        value=(
            f"**Chat** `{chat_model}` ({chat_provider})\n"
            f"**Local default** `{basic_local_model}`\n"
            f"**News** {news_line}"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Configuration",
        value=(
            f"**Persona** `{get_current_persona()}`\n"
            f"**Wake word** `{wake}` · **Home channel** {home_line}"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Environment",
        value=f"**Home Assistant** {ha_line}\n**Location** {loc}"[:1024],
        inline=False,
    )

    if include_hardware and sys_status and "error" not in sys_status:
        bot_uptime_sec = time.time() - client.start_time
        b_days = int(bot_uptime_sec // 86400)
        b_hours = int((bot_uptime_sec % 86400) // 3600)
        b_mins = int((bot_uptime_sec % 3600) // 60)
        bot_uptime = f"{b_days}d {b_hours}h {b_mins}m" if b_days else f"{b_hours}h {b_mins}m"
        sys_block = (
            f"**IP** `{sys_status['ip_address']}` · **Host** `{sys_status['hostname']}`\n"
            f"**OS** {sys_status['os']}\n"
            f"**CPU** {sys_status['cpu_percent']}% · **RAM** {sys_status['memory_used']}/{sys_status['memory_total']} MB · "
            f"**Disk** {sys_status['disk_used']}/{sys_status['disk_total']} GB\n"
            f"**GPU** {sys_status.get('gpu_util', 'N/A')} · {sys_status['gpu_temp']}\n"
            f"**Bot uptime** {bot_uptime} · **System uptime** {sys_status['uptime']}"
        )[:1024]
        embed.add_field(name="System", value=sys_block, inline=False)

    embed.add_field(
        name="Platform",
        value=(
            f"**Ollama** {ollama_line}\n"
            f"**Background services** reminders, news, local status API (`http://localhost:{STATUS_PORT}/status`)\n"
            f"**Commands** {cmd_line}"
        )[:1024],
        inline=False,
    )

    if include_hardware and sys_status and "error" not in sys_status:
        embed.set_footer(text=f"Python {sys_status['python_version']} · /help")

    return errors, embed
