import re
from datetime import datetime, timedelta
from typing import Optional

_UNIT_RE = re.compile(r"(?:in\s+)?(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)\b")
_CLOCK_RE = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"


def _unit_delta(amount: int, unit: str) -> timedelta:
    if unit.startswith("m"):
        return timedelta(minutes=amount)
    if unit.startswith("h"):
        return timedelta(hours=amount)
    if unit.startswith("d"):
        return timedelta(days=amount)
    return timedelta(weeks=amount)


def _clock(base: datetime, m: re.Match) -> Optional[datetime]:
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    period = (m.group(3) or "").lower()
    if period == "pm" and hour < 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_time_string(time_str: str) -> Optional[datetime]:
    """'in 5 minutes', '2h', 'tomorrow 9am', 'at 15:30', or anything dateparser understands."""
    now = datetime.now()
    s = time_str.strip().lower()

    m = _UNIT_RE.match(s)
    if m:
        return now + _unit_delta(int(m.group(1)), m.group(2))

    if "tomorrow" in s:
        base = now + timedelta(days=1)
        hm = re.search(_CLOCK_RE, s)
        if hm:
            return _clock(base, hm)
        return base.replace(hour=9, minute=0, second=0, microsecond=0)

    m = re.match(r"at\s+" + _CLOCK_RE, s)
    if m:
        target = _clock(now, m)
        if target is not None and target <= now:
            target += timedelta(days=1)
        return target

    try:
        import dateparser
    except ImportError:
        return None
    parsed = dateparser.parse(time_str, settings={"PREFER_DATES_FROM": "future"})
    if parsed and parsed > now:
        return parsed
    return None
