"""Daily recurring quiet hours for news DMs (server local timezone)."""

from typing import Optional

import discord
from discord import app_commands
from whitelist import get_user_permission
from datetime import datetime

from services.news_service import (
    clear_quiet_time,
    get_daily_quiet_schedule,
    get_user_topics,
    parse_time_of_day,
    set_daily_quiet_schedule,
    user_in_quiet_window,
    format_minutes_as_clock,
)


def register(client: discord.Client):
    @client.tree.command(
        name="news-time",
        description="Set daily quiet hours for news DMs (e.g. pause 1:00, resume 9:00)",
    )
    @app_commands.describe(
        resume="When notifications turn ON again each day (e.g. 9.00 = 9:00 AM)",
        pause="When notifications turn OFF each day (e.g. 1.00 = 1:00 AM)",
        cancel="Remove daily quiet hours",
    )
    @app_commands.choices(cancel=[
        app_commands.Choice(name="Yes, clear quiet hours", value="yes"),
    ])
    async def news_time(
        interaction: discord.Interaction,
        resume: Optional[str] = None,
        pause: Optional[str] = None,
        cancel: Optional[app_commands.Choice[str]] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        if cancel:
            sched = get_daily_quiet_schedule(interaction.user.id)
            if sched:
                clear_quiet_time(interaction.user.id)
                await interaction.response.send_message(
                    "✅ Daily quiet hours removed. News will be delivered at any time.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You don't have daily quiet hours set.",
                    ephemeral=True,
                )
            return

        topics = get_user_topics(interaction.user.id)

        # Status: no resume/pause args
        if not resume and not pause:
            sched = get_daily_quiet_schedule(interaction.user.id)
            in_quiet = user_in_quiet_window(interaction.user.id)

            if sched:
                pause_m, resume_m = sched
                p_str = format_minutes_as_clock(pause_m)
                r_str = format_minutes_as_clock(resume_m)
                embed = discord.Embed(
                    title="🔕 Daily quiet hours",
                    color=0xF39C12,
                )
                embed.add_field(
                    name="Schedule (server local time)",
                    value=f"**Off (quiet):** {p_str} → **{r_str}**\n**On:** the rest of the day",
                    inline=False,
                )
                embed.add_field(
                    name="Right now",
                    value="🔕 In quiet hours — articles are queued" if in_quiet else "🔔 Notifications active",
                    inline=False,
                )
                embed.add_field(
                    name="Subscriptions",
                    value=", ".join(f"`{t}`" for t in topics) if topics else "None",
                    inline=False,
                )
                embed.add_field(
                    name="Change",
                    value="`/news-time resume:9.00 pause:1.00` — quiet from 1:00 to 9:00 every day\n"
                          "`/news-time cancel:yes` — remove schedule",
                    inline=False,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(
                    title="🔔 No quiet hours",
                    description="News DMs are sent whenever new items appear (subject to fetch interval).",
                    color=0x2ECC71,
                )
                embed.add_field(
                    name="Subscriptions",
                    value=", ".join(f"`{t}`" for t in topics) if topics else "None",
                    inline=False,
                )
                embed.add_field(
                    name="Set daily quiet window",
                    value=(
                        "**First** `resume` — time notifications **turn on** again.\n"
                        "**Second** `pause` — time they **turn off**.\n\n"
                        "Example: `resume:9.00` `pause:1.00` → **quiet from 1:00 to 9:00** every day "
                        "(server clock).\n"
                        "Supports `9.00`, `9:00`, `21:30`."
                    ),
                    inline=False,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not resume or not pause:
            await interaction.response.send_message(
                "❌ Set **both** `resume` and `pause`, e.g. `/news-time resume:9.00 pause:1.00` "
                "(quiet 1:00–9:00 daily).",
                ephemeral=True,
            )
            return

        resume_min = parse_time_of_day(resume)
        pause_min = parse_time_of_day(pause)
        if resume_min is None or pause_min is None:
            await interaction.response.send_message(
                "❌ Could not parse times. Use e.g. `9.00`, `9:00`, `1.00`, `21:30`.",
                ephemeral=True,
            )
            return

        if resume_min == pause_min:
            await interaction.response.send_message(
                "❌ `resume` and `pause` must be different times.",
                ephemeral=True,
            )
            return

        set_daily_quiet_schedule(interaction.user.id, resume_min, pause_min)

        p_str = format_minutes_as_clock(pause_min)
        r_str = format_minutes_as_clock(resume_min)
        embed = discord.Embed(
            title="🔕 Daily quiet hours saved",
            color=0xF39C12,
            description=f"**Server local time** — notifications **off** from **{p_str}** to **{r_str}** each day.",
        )
        embed.add_field(
            name="When you get DMs",
            value=f"Outside that window (including after **{r_str}** until **{p_str}**).",
            inline=False,
        )
        embed.add_field(
            name="Digest",
            value="Queued items are summarized when quiet hours end (at **" + r_str + "**).",
            inline=False,
        )
        embed.set_footer(text=f"Today: {datetime.now().strftime('%Y-%m-%d %H:%M')} on this server")
        await interaction.response.send_message(embed=embed, ephemeral=True)
