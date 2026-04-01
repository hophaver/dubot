"""Build iCalendar (.ics) documents for recurring weekday events in local time."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, List


_WEEKDAY_ALIASES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def parse_weekdays(s: str) -> set[int]:
    """Return a set of Python weekday ints (Monday=0 .. Sunday=6)."""
    raw = (s or "").strip().lower()
    if not raw:
        raise ValueError("weekdays cannot be empty")
    if raw in ("weekdays", "weekday", "mon-fri", "monfri"):
        return {0, 1, 2, 3, 4}
    if raw in ("weekend", "weekends"):
        return {5, 6}
    if raw in ("all", "everyday", "daily"):
        return {0, 1, 2, 3, 4, 5, 6}
    out: set[int] = set()
    for part in re.split(r"[,;\s]+", raw):
        part = part.strip()
        if not part:
            continue
        if part not in _WEEKDAY_ALIASES:
            raise ValueError(
                f"unknown weekday '{part}'; use mon,tue,wed,thu,fri,sat,sun (comma-separated)"
            )
        out.add(_WEEKDAY_ALIASES[part])
    if not out:
        raise ValueError("no valid weekdays parsed")
    return out


def parse_hhmm(s: str) -> time:
    s = (s or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        raise ValueError("time must be 24h HH:MM (e.g. 09:30 or 14:00)")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError("time out of range")
    return time(h, mi, 0)


def parse_iso_date(s: str) -> date:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError("date must be YYYY-MM-DD (or DD.MM.YYYY / DD/MM/YYYY)")


def get_local_tzinfo():
    """Timezone of the machine running the process (same as datetime.now().astimezone())."""
    return datetime.now().astimezone().tzinfo


def format_ical_datetime(dt: datetime) -> str:
    """RFC 5545 local form with offset, e.g. 20260401T093000+0300."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = dt.strftime("%Y%m%dT%H%M%S")
    off = dt.utcoffset() or timedelta(0)
    secs = int(off.total_seconds())
    sign = "+" if secs >= 0 else "-"
    secs = abs(secs)
    hh, mm = secs // 3600, (secs % 3600) // 60
    return f"{s}{sign}{hh:02d}{mm:02d}"


def escape_ical_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def fold_ical_line(line: str) -> str:
    if len(line) <= 75:
        return line
    parts: List[str] = []
    while len(line) > 75:
        parts.append(line[:75])
        line = " " + line[75:]
    parts.append(line)
    return "\r\n".join(parts)


def iter_event_dates(start: date, end: date, weekdays: set[int]) -> Iterable[date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    d = start
    one = timedelta(days=1)
    while d <= end:
        if d.weekday() in weekdays:
            yield d
        d += one


def build_calendar_ics(
    title: str,
    start: date,
    end: date,
    weekdays: set[int],
    at_time: time,
    duration_minutes: int,
    random_offsets_minutes: List[int],
    *,
    tz=None,
) -> str:
    """
    random_offsets_minutes must have one entry per generated event (same order as iteration).
    """
    tz = tz or get_local_tzinfo()
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//dubot//cal//EN",
        "CALSCALE:GREGORIAN",
    ]
    dates = list(iter_event_dates(start, end, weekdays))
    if len(dates) != len(random_offsets_minutes):
        raise ValueError("internal: offsets list length mismatch")
    stamp = format_ical_datetime(datetime.now(timezone.utc))
    summary = escape_ical_text(title)
    for d, off_min in zip(dates, random_offsets_minutes):
        start_dt = datetime.combine(d, at_time, tzinfo=tz) + timedelta(minutes=off_min)
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        uid = f"{uuid.uuid4()}@dubot-cal"
        ve = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{format_ical_datetime(start_dt)}",
            f"DTEND:{format_ical_datetime(end_dt)}",
            fold_ical_line(f"SUMMARY:{summary}"),
            "END:VEVENT",
        ]
        lines.extend(ve)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
