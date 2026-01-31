import re
from datetime import datetime, timedelta
from typing import Optional


def parse_time_string(time_str: str) -> Optional[datetime]:
    now = datetime.now()
    s = time_str.strip().lower()

    m = re.match(r'(?:in\s+)?(\d+)\s+(minute|hour|day|week)s?\b', s)
    if m:
        amount, unit = int(m.group(1)), m.group(2)
        if unit == 'minute': return now + timedelta(minutes=amount)
        if unit == 'hour': return now + timedelta(hours=amount)
        if unit == 'day': return now + timedelta(days=amount)
        if unit == 'week': return now + timedelta(weeks=amount)

    m = re.match(r'(?:in\s+)?(\d+)\s*(m|min|h|hr|hrs|d|day|days|w|week)s?\b', s)
    if m:
        amount, u = int(m.group(1)), m.group(2)
        if u in ('m', 'min', 'mins'): return now + timedelta(minutes=amount)
        if u in ('h', 'hr', 'hrs'): return now + timedelta(hours=amount)
        if u in ('d', 'day', 'days'): return now + timedelta(days=amount)
        if u in ('w', 'week', 'weeks'): return now + timedelta(weeks=amount)

    if 'tomorrow' in s:
        base = now + timedelta(days=1)
        hm = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', s)
        if hm:
            hour, minute = int(hm.group(1)), int(hm.group(2) or 0)
            period = (hm.group(3) or '').lower()
            if period == 'pm' and hour < 12: hour += 12
            elif period == 'am' and hour == 12: hour = 0
            return base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return base.replace(hour=9, minute=0, second=0, microsecond=0)

    m = re.match(r'at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        period = (m.group(3) or '').lower()
        if period == 'pm' and hour < 12: hour += 12
        elif period == 'am' and hour == 12: hour = 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    try:
        import dateparser
        parsed = dateparser.parse(time_str, settings={'PREFER_DATES_FROM': 'future'})
        if parsed and parsed > now:
            return parsed
    except Exception:
        pass
    return None
