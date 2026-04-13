"""Cursor team spend command (requires Cursor Admin API key)."""

from datetime import datetime, timezone

import discord
from whitelist import get_user_permission
from utils.cursor_api import fetch_spend_summary


def _fmt_usd_from_cents(cents: int) -> str:
    dollars = cents / 100.0
    return f"${dollars:,.2f}"


def register(client):
    @client.tree.command(
        name="cursor",
        description="Check Cursor team spend for the current billing cycle",
    )
    async def cursor(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        data, err = fetch_spend_summary()
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        cycle_start = "Unknown"
        cycle_start_ms = data.get("cycle_start_ms")
        try:
            if cycle_start_ms is not None:
                dt = datetime.fromtimestamp(float(cycle_start_ms) / 1000.0, tz=timezone.utc)
                cycle_start = dt.strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            cycle_start = "Unknown"

        embed = discord.Embed(
            title="Cursor spend (current cycle)",
            color=discord.Color.blurple(),
            description="Totals from Cursor Admin API (`POST /teams/spend`).",
        )
        embed.add_field(
            name="On-demand spend",
            value=_fmt_usd_from_cents(int(data["on_demand_cents"])),
            inline=True,
        )
        embed.add_field(
            name="Overall spend",
            value=_fmt_usd_from_cents(int(data["overall_cents"])),
            inline=True,
        )
        embed.add_field(
            name="Included usage (est.)",
            value=_fmt_usd_from_cents(int(data["included_cents"])),
            inline=True,
        )
        embed.add_field(name="Team members", value=str(data["total_members"]), inline=True)
        embed.add_field(name="Cycle start", value=cycle_start, inline=True)
        embed.set_footer(
            text="Set CURSOR_USER_API_KEY in .env (fallback: CURSOR_API_KEY)"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
