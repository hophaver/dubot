"""Pause news notifications for a set time, get a summary when it ends."""

from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from datetime import datetime, timedelta
from services.news_service import (
    set_quiet_time,
    get_quiet_time,
    clear_quiet_time,
    get_user_topics,
)


def _parse_duration(text: str) -> Optional[timedelta]:
    """Parse a human duration like '2h', '30m', '1d', '2h30m', '1 day'."""
    text = text.lower().strip()
    total_minutes = 0

    import re
    # Try patterns like 2h30m, 1d, 45m, 3h, 1 day, 2 hours, etc.
    day_match = re.findall(r"(\d+)\s*d(?:ays?)?", text)
    hour_match = re.findall(r"(\d+)\s*h(?:ours?)?", text)
    min_match = re.findall(r"(\d+)\s*m(?:in(?:utes?)?)?", text)

    for d in day_match:
        total_minutes += int(d) * 1440
    for h in hour_match:
        total_minutes += int(h) * 60
    for m in min_match:
        total_minutes += int(m)

    # Plain number = hours
    if total_minutes == 0:
        try:
            total_minutes = int(float(text)) * 60
        except ValueError:
            return None

    if total_minutes <= 0:
        return None

    return timedelta(minutes=total_minutes)


def register(client: discord.Client):
    @client.tree.command(
        name="news-time",
        description="Pause news notifications for a set time (get a summary when it ends)",
    )
    @app_commands.describe(
        duration="How long to pause (e.g. '2h', '30m', '1d', '8h30m') — leave blank to check/cancel",
        cancel="Cancel an active quiet time",
    )
    @app_commands.choices(cancel=[
        app_commands.Choice(name="Yes, cancel quiet time", value="yes"),
    ])
    async def news_time(
        interaction: discord.Interaction,
        duration: Optional[str] = None,
        cancel: Optional[app_commands.Choice[str]] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        # Cancel
        if cancel:
            existing = get_quiet_time(interaction.user.id)
            if existing:
                clear_quiet_time(interaction.user.id)
                await interaction.response.send_message(
                    "✅ Quiet time cancelled. News notifications will resume immediately.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You don't have an active quiet time.",
                    ephemeral=True,
                )
            return

        # Check current status
        if not duration:
            existing = get_quiet_time(interaction.user.id)
            topics = get_user_topics(interaction.user.id)

            if existing and existing > datetime.now():
                remaining = existing - datetime.now()
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes = remainder // 60

                embed = discord.Embed(
                    title="🔕 Quiet Time Active",
                    color=0xF39C12,
                )
                embed.add_field(
                    name="Ends at",
                    value=f"<t:{int(existing.timestamp())}:F> (<t:{int(existing.timestamp())}:R>)",
                    inline=False,
                )
                embed.add_field(
                    name="Remaining",
                    value=f"{hours}h {minutes}m",
                    inline=True,
                )
                embed.add_field(
                    name="Subscriptions",
                    value=", ".join(f"`{t}`" for t in topics) if topics else "None",
                    inline=True,
                )
                embed.set_footer(text="Use /news-time cancel:yes to resume immediately")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(
                    title="🔔 Notifications Active",
                    description="You don't have quiet time enabled. News will be delivered as it arrives.",
                    color=0x2ECC71,
                )
                embed.add_field(
                    name="Subscriptions",
                    value=", ".join(f"`{t}`" for t in topics) if topics else "None",
                    inline=False,
                )
                embed.add_field(
                    name="Pause notifications",
                    value="`/news-time duration:2h` — pause for 2 hours\n"
                          "`/news-time duration:8h30m` — pause overnight\n"
                          "`/news-time duration:1d` — pause for a day",
                    inline=False,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Set quiet time
        delta = _parse_duration(duration)
        if not delta:
            await interaction.response.send_message(
                "❌ Could not parse duration. Try `2h`, `30m`, `1d`, `8h30m`.",
                ephemeral=True,
            )
            return

        if delta > timedelta(days=7):
            await interaction.response.send_message(
                "❌ Maximum quiet time is 7 days.",
                ephemeral=True,
            )
            return

        until = datetime.now() + delta
        set_quiet_time(interaction.user.id, until)

        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        duration_str = ""
        if hours > 0:
            duration_str += f"{hours}h "
        if minutes > 0:
            duration_str += f"{minutes}m"
        duration_str = duration_str.strip() or "< 1m"

        embed = discord.Embed(
            title="🔕 Quiet Time Enabled",
            description="News notifications are paused. You'll get a summary of everything you missed when it ends.",
            color=0xF39C12,
        )
        embed.add_field(
            name="Duration",
            value=duration_str,
            inline=True,
        )
        embed.add_field(
            name="Resumes at",
            value=f"<t:{int(until.timestamp())}:F> (<t:{int(until.timestamp())}:R>)",
            inline=True,
        )
        embed.set_footer(text="Use /news-time cancel:yes to resume early")

        await interaction.response.send_message(embed=embed, ephemeral=True)
