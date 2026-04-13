import io
import random
from typing import Optional

import discord
from discord import app_commands
from whitelist import get_user_permission

from utils.ical_batch import (
    build_calendar_ics,
    get_local_tzinfo,
    iter_event_dates,
    parse_hhmm,
    parse_iso_date,
    parse_weekdays,
)

_MAX_EVENTS = 1000


def register(client: discord.Client):
    @client.tree.command(
        name="cal",
        description="Create recurring calendar events between two dates (.ics export)",
    )
    @app_commands.rename(
        start_date="startdate",
        end_date="enddate",
        at_time="time",
        duration_minutes="duration",
    )
    @app_commands.describe(
        title="Event title",
        start_date="First day to consider (YYYY-MM-DD)",
        end_date="Last day to consider, inclusive (YYYY-MM-DD)",
        weekdays="Days: mon,tue,wed,thu,fri,sat,sun (comma-separated), or weekdays / weekend / all",
        at_time="Start time each day, 24h (e.g. 09:30)",
        duration_minutes="Event length in minutes",
        random_variation="Optional: each start time shifts by a random integer in [-n, n] minutes",
    )
    async def cal_command(
        interaction: discord.Interaction,
        title: str,
        start_date: str,
        end_date: str,
        weekdays: str,
        at_time: str,
        duration_minutes: app_commands.Range[int, 1, 24 * 60],
        random_variation: Optional[app_commands.Range[int, 0, 720]] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            s = parse_iso_date(start_date)
            e = parse_iso_date(end_date)
            days = parse_weekdays(weekdays)
            t = parse_hhmm(at_time)
            n = int(random_variation) if random_variation is not None else 0
            if len(title) > 500:
                await interaction.followup.send("❌ Title is too long (max 500 characters).")
                return

            dates = list(iter_event_dates(s, e, days))
            if not dates:
                await interaction.followup.send("❌ No matching weekdays in that date range.")
                return
            if len(dates) > _MAX_EVENTS:
                await interaction.followup.send(
                    f"❌ That would create {len(dates)} events; maximum is {_MAX_EVENTS}. "
                    "Narrow the range or weekdays."
                )
                return

            offsets = [random.randint(-n, n) for _ in dates]
            tz = get_local_tzinfo()
            ics_body = build_calendar_ics(
                title.strip(),
                s,
                e,
                days,
                t,
                int(duration_minutes),
                offsets,
                tz=tz,
            )
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title.strip()[:40]).strip("_") or "events"
            filename = f"{safe}.ics"
            fp = io.BytesIO(ics_body.encode("utf-8"))
            fp.seek(0)
            file = discord.File(fp, filename=filename)

            tz_label = str(tz) if tz else "local"
            embed = discord.Embed(
                title="📅 Calendar file ready",
                description="Import the attached `.ics` into Apple Calendar, Google Calendar, Outlook, etc.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Events", value=str(len(dates)), inline=True)
            embed.add_field(name="Timezone", value=f"`{tz_label}` (this machine)", inline=True)
            if n:
                embed.add_field(
                    name="Random offset",
                    value=f"±{n} min per event (applied to start time only)",
                    inline=False,
                )
            embed.set_footer(text="One VEVENT per matching day in the range")
            await interaction.followup.send(embed=embed, file=file)
        except ValueError as err:
            await interaction.followup.send(f"❌ {err}")
        except Exception as exc:
            await interaction.followup.send(f"❌ Error: {str(exc)[:200]}")
