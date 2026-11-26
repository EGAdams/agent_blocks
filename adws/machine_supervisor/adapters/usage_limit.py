"""IUsageLimit for the Claude CLI: it answers "You've hit your 5-hour limit · resets 3:50pm (America/New_York)"
(with is_error set) when the subscription's usage limit is used up. That is a reason to wait, not a failure.

A reset time that cannot be read means: try again in an hour. A few minutes are added either way, so the next
try does not land on the minute the limit is still in force.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LIMIT = re.compile(r"hit your\b[^\n]{0,40}\blimit", re.IGNORECASE)
RESETS = re.compile(r"resets\b[^\n]*?\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b(?:[^\n(]*\(([A-Za-z_/+-]+)\))?",
                    re.IGNORECASE)
MARGIN = timedelta(minutes=5)
UNREAD = timedelta(hours=1)


class ClaudeUsageLimit:
    def until(self, said: str, now: datetime) -> datetime | None:
        if not said or not LIMIT.search(said):
            return None
        found = RESETS.search(said)
        if found is None:
            return now + UNREAD
        hour, minute, half, zone = found.groups()
        try:
            where = ZoneInfo(zone) if zone else timezone.utc
        except (ZoneInfoNotFoundError, ValueError):
            where = timezone.utc
        hour = int(hour) % 12 + (12 if half.lower() == "pm" else 0)
        local = now.astimezone(where)
        reset = local.replace(hour=hour, minute=int(minute or 0), second=0, microsecond=0)
        if reset <= local:
            reset += timedelta(days=1)
        return reset.astimezone(timezone.utc) + MARGIN
