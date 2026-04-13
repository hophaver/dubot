"""OpenRouter credits balance (management API key)."""

import discord
from discord import app_commands
from whitelist import get_user_permission
from utils.openrouter import fetch_credits


def _fmt_money(n: float) -> str:
    if abs(n - round(n)) < 1e-6:
        return f"{int(round(n)):,}"
    return f"{n:,.4f}".rstrip("0").rstrip(".")


def register(client):
    @client.tree.command(
        name="bal",
        description="Check OpenRouter account credits (requires management API key in .env)",
    )
    async def bal(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        data, err = fetch_credits()
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        total = data["total_credits"]
        used = data["total_usage"]
        rem = data["remaining"]

        embed = discord.Embed(
            title="OpenRouter credits",
            color=discord.Color.blurple(),
            description="Totals from OpenRouter (`GET /api/v1/credits`).",
        )
        embed.add_field(name="Purchased (total)", value=_fmt_money(total), inline=True)
        embed.add_field(name="Used", value=_fmt_money(used), inline=True)
        embed.add_field(name="Remaining", value=_fmt_money(rem), inline=True)
        embed.set_footer(text="Set OPENROUTER_API_KEY in .env (management key)")

        await interaction.followup.send(embed=embed, ephemeral=True)
