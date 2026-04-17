"""Discord /trader slash command group → Trader FastAPI (/status, /command)."""

from __future__ import annotations

import json
from typing import Any, List, Optional

import discord
from discord import app_commands

from whitelist import get_user_permission
from services import trader_client


def _setup_guide_embed() -> discord.Embed:
    emb = discord.Embed(
        title="Trader · Setup guide",
        color=discord.Color.dark_blue(),
    )
    emb.add_field(
        name="1. Trader host",
        value=(
            "Set **`TRADER_BASE_URL`** on the bot host (env or `.env`) to your Trader service root, "
            "including scheme and port, **no trailing slash**.\n"
            "Example: `https://192.168.1.50:9000` or `https://trader.example.com`"
        ),
        inline=False,
    )
    emb.add_field(
        name="2. Shared secret",
        value=(
            "Set the **same** value on the Trader and the bot: **`TRADER_AUTH_TOKEN`** "
            "(aliases: `TRADER_SHARED_SECRET`, `TRADER_SECRET`).\n"
            "The bot sends it on **every** request as header **`X-Trader-Auth-Token`**."
        ),
        inline=False,
    )
    emb.add_field(
        name="3. API the Trader must expose",
        value=(
            "**`GET /status`** — JSON with (at minimum) fields the bot can summarize: "
            "PnL, equity, positions (see `/trader status`).\n"
            "**`POST /command`** — JSON commands from the bot:\n"
            "• `{ \"risk_multiplier\": <number> }`\n"
            "• `{ \"trading_mode\": \"paper\" | \"live\" }`\n"
            "• `{ \"killswitch\": true }` (emergency stop)\n"
            "All requests from the bot include **`X-Trader-Auth-Token`**."
        ),
        inline=False,
    )
    emb.add_field(
        name="4. Real-time updates (optional)",
        value=(
            "Configure the Trader to **POST** JSON events to the bot webhook URL "
            "(see env **`TRADER_WEBHOOK_PORT`**) at path **`/trader/webhook`**, "
            "with the same **`X-Trader-Auth-Token`** header.\n"
            "Set **`TRADER_WEBHOOK_CHANNEL_ID`** to the Discord channel ID for alerts "
            "(or rely on **`/sethome`** startup channel).\n"
            "Suggested JSON: `{ \"event\": \"buy\"|\"sell\"|\"error\", \"symbol\": \"…\", \"message\": \"…\" }`"
        ),
        inline=False,
    )
    emb.add_field(
        name="5. Slash commands (Discord)",
        value=(
            "Discord requires a **subcommand** for grouped commands.\n"
            "Use **`/trader setup`** for this guide; **`/trader status`**, **`/trader risk`**, "
            "**`/trader mode`**, **`/trader kill`** for control."
        ),
        inline=False,
    )
    emb.set_footer(text="Without TRADER_BASE_URL + TRADER_AUTH_TOKEN, /trader actions will fail until configured.")
    return emb


def _coerce_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _format_positions_block(data: Any, max_len: int = 3500) -> str:
    if data is None:
        return "—"
    if isinstance(data, str):
        return data[:max_len]
    if isinstance(data, list):
        lines: List[str] = []
        for i, p in enumerate(data[:40]):
            if isinstance(p, dict):
                sym = p.get("symbol") or p.get("ticker") or p.get("pair") or "?"
                side = p.get("side") or p.get("direction") or ""
                qty = p.get("qty") or p.get("quantity") or p.get("size") or ""
                entry = p.get("entry") or p.get("entry_price") or p.get("avg_price") or ""
                u_pnl = p.get("unrealized_pnl") or p.get("pnl") or p.get("u_pnl") or ""
                lines.append(f"{i+1}. {sym} {side} qty={qty} entry={entry} uPnL={u_pnl}".strip())
            else:
                lines.append(f"{i+1}. {p}")
        body = "\n".join(lines) if lines else "—"
        return body[:max_len]
    if isinstance(data, dict):
        return json.dumps(data, indent=0, default=str)[:max_len]
    return str(data)[:max_len]


def _pick_first(data: dict, keys: tuple) -> Any:
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
    return None


def _status_embed(data: Any, http_status: int) -> discord.Embed:
    if not isinstance(data, dict):
        return discord.Embed(
            title="Trader · Status",
            description=f"HTTP {http_status}\n```{(str(data)[:900])}```",
            color=discord.Color.dark_red() if http_status >= 400 else discord.Color.greyple(),
        )
    pnl = _pick_first(data, ("pnl", "PnL", "PNL", "total_pnl", "realized_pnl", "unrealized_pnl", "profit"))
    eq = _pick_first(data, ("equity", "Equity", "account_equity", "balance", "net_liquidation"))
    mode = _pick_first(data, ("mode", "trading_mode", "environment", "paper"))
    risk = _pick_first(data, ("risk_multiplier", "risk", "riskMultiplier"))
    positions = _pick_first(data, ("positions", "open_positions", "active_positions", "orders"))

    title = "Trader · Status"
    color = discord.Color.dark_teal()
    emb = discord.Embed(title=title, color=color)
    emb.add_field(name="PnL", value=f"`{pnl}`" if pnl is not None else "—", inline=True)
    emb.add_field(name="Equity", value=f"`{eq}`" if eq is not None else "—", inline=True)
    if mode is not None:
        emb.add_field(name="Mode", value=f"`{mode}`", inline=True)
    if risk is not None:
        emb.add_field(name="Risk ×", value=f"`{risk}`", inline=True)
    pos_txt = _format_positions_block(positions)
    emb.add_field(name="Positions", value=pos_txt or "—", inline=False)
    if http_status != 200:
        emb.set_footer(text=f"HTTP {http_status}")
    return emb


def register(client: discord.Client) -> None:
    trader = app_commands.Group(name="trader", description="Connect to your Trader FastAPI service")

    @trader.command(name="setup", description="Setup guide: host, token, API paths, webhook")
    async def trader_setup(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.send_message(embed=_setup_guide_embed(), ephemeral=True)

    @trader.command(name="status", description="Fetch Trader /status (PnL, equity, positions)")
    async def trader_status(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not trader_client.trader_client_configured():
            await interaction.response.send_message(
                "⚠️ Trader is not configured. Use `/trader setup` and set **TRADER_BASE_URL** + **TRADER_AUTH_TOKEN**.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=False)
        try:
            code, data = await trader_client.fetch_trader_status_json()
        except Exception as e:
            await interaction.followup.send(f"❌ Trader request failed: {e}")
            return
        await interaction.followup.send(embed=_status_embed(data, code))

    @trader.command(name="risk", description="POST /command to set risk_multiplier")
    @app_commands.describe(value="Risk multiplier (e.g. 0.5 or 1.0)")
    async def trader_risk(interaction: discord.Interaction, value: float):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not trader_client.trader_client_configured():
            await interaction.response.send_message("⚠️ Trader is not configured. See `/trader setup`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)
        try:
            code, data = await trader_client.post_trader_command({"risk_multiplier": float(value)})
        except Exception as e:
            await interaction.followup.send(f"❌ Trader request failed: {e}")
            return
        await interaction.followup.send(
            embed=discord.Embed(
                title="Trader · Risk",
                description=f"HTTP **{code}**\n```json\n{json.dumps(data, indent=2, default=str)[:3500]}\n```",
                color=discord.Color.green() if code < 400 else discord.Color.red(),
            )
        )

    mode_choice = app_commands.Choice(name="paper", value="paper")
    mode_choice2 = app_commands.Choice(name="live", value="live")

    @trader.command(name="mode", description="POST /command to set trading_mode paper or live")
    @app_commands.describe(mode="Trading mode")
    @app_commands.choices(mode=[mode_choice, mode_choice2])
    async def trader_mode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not trader_client.trader_client_configured():
            await interaction.response.send_message("⚠️ Trader is not configured. See `/trader setup`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)
        try:
            code, data = await trader_client.post_trader_command({"trading_mode": mode.value})
        except Exception as e:
            await interaction.followup.send(f"❌ Trader request failed: {e}")
            return
        await interaction.followup.send(
            embed=discord.Embed(
                title="Trader · Mode",
                description=f"Set **{mode.value}** — HTTP **{code}**\n```json\n{json.dumps(data, indent=2, default=str)[:3500]}\n```",
                color=discord.Color.green() if code < 400 else discord.Color.red(),
            )
        )

    @trader.command(name="kill", description="POST /command — emergency killswitch")
    async def trader_kill(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not trader_client.trader_client_configured():
            await interaction.response.send_message("⚠️ Trader is not configured. See `/trader setup`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)
        try:
            code, data = await trader_client.post_trader_command({"killswitch": True})
        except Exception as e:
            await interaction.followup.send(f"❌ Trader request failed: {e}")
            return
        await interaction.followup.send(
            embed=discord.Embed(
                title="Trader · Killswitch",
                description=f"HTTP **{code}**\n```json\n{json.dumps(data, indent=2, default=str)[:3500]}\n```",
                color=discord.Color.dark_red() if code < 400 else discord.Color.red(),
            )
        )

    client.tree.add_command(trader)
