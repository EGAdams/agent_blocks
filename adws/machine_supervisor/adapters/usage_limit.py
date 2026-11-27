"""IUsageLimit for the Claude CLI, and below it the Codex CLI. Claude answers "You've hit your 5-hour limit ·
resets 3:50pm (America/New_York)" (with is_error set) when the subscription's usage limit is used up. That is a reason to wait, not a failure.

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


MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
TRY_AT = re.compile(r"try again at\s+(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})"
                    r"(?:st|nd|rd|th)?,?\s+(?:(\d{4})\s+)?(?:at\s+)?)?(\d{1,2}):(\d{2})\s*(am|pm)", re.IGNORECASE)
TRY_IN = re.compile(r"try again in\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m)\b[\s,and]*)+)",
                    re.IGNORECASE)
UNIT = re.compile(r"(\d+)\s*(d|h|m)", re.IGNORECASE)


class CodexUsageLimit:
    """IUsageLimit for the Codex CLI on a ChatGPT login. It says "You've hit your usage limit. ... try again at
    3:50 PM" (the machine's local time; "Nov 28 3:50 PM" when it is another day, with a year when it is another
    year), or "try again in 2 hours 5 minutes", or "try again later". Unreadable means an hour, as for Claude."""
    def __init__(self, zone: timezone | ZoneInfo | None = None) -> None:
        self._zone = zone

    def until(self, said: str, now: datetime) -> datetime | None:
        if not said or not LIMIT.search(said):
            return None
        if (found := TRY_AT.search(said)) is not None:
            return self._at(found, now) + MARGIN
        if (found := TRY_IN.search(said)) is not None:
            spans = {"d": timedelta(days=1), "h": timedelta(hours=1), "m": timedelta(minutes=1)}
            wait = sum((int(n) * spans[u.lower()] for n, u in UNIT.findall(found.group(1))), timedelta())
            return now + wait + MARGIN
        return now + UNREAD

    def _at(self, found: re.Match, now: datetime) -> datetime:
        month, day, year, hour, minute, half = found.groups()
        local = now.astimezone(self._zone) if self._zone else now.astimezone()
        hour = int(hour) % 12 + (12 if half.lower() == "pm" else 0)
        reset = local.replace(hour=hour, minute=int(minute), second=0, microsecond=0)
        if month:
            try:
                reset = reset.replace(year=int(year) if year else local.year,
                                      month=MONTHS.index(month.lower()[:3]) + 1, day=int(day))
            except ValueError:
                return now + UNREAD
            if not year and reset < local - timedelta(days=1):
                reset = reset.replace(year=reset.year + 1)
        elif reset <= local:
            reset += timedelta(days=1)
        return reset.astimezone(timezone.utc)
