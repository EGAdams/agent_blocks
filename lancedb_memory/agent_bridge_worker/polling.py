"""How often the worker asks the gateway for work. Pure logic, no I/O.

HostGator's cheapest plan doesn't publish its limits, so every idle check is a
real PHP request we pay for. The numbers live in `PollSettings` (loaded from
`poll_settings.json`), and the behaviour is split into GoF pieces so a change is a
setting or one new class, never an edit to the loop:

- Strategy  -- `IPollSchedule`: "given what just happened, how long to wait".
               `BurstAfterWork` is the base cadence (fast just after a task, slow when idle).
- Decorator -- `ErrorBackoff` and `ActiveHoursOnly` wrap any schedule and only
               lengthen its answer; stack them, drop them, add another.
- Factory   -- `build_schedule( settings )` is the one place that chooses and stacks them.

`PollLoop` (in `watch.py`) asks the schedule after every check and never decides a delay itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ports import GatewayError


class ActiveHours( BaseModel ):
    """Poll only from `start_hour` to `end_hour`, local time (24 means midnight).

    A window whose end is earlier than its start runs overnight: 22 to 6 checks
    from 22:00, through midnight, until 06:00. Equal hours are refused, because
    "from 9 to 9" is ambiguous; around the clock is `active_hours: null`.
    """

    model_config = ConfigDict( extra="forbid", frozen=True )

    start_hour: int = Field( default=7, ge=0, le=23 )
    end_hour:   int = Field( default=24, ge=1, le=24 )

    @model_validator( mode="after" )
    def _ordered( self ) -> "ActiveHours":
        if self.end_hour % 24 == self.start_hour:
            raise ValueError( "start_hour and end_hour must differ (for around the clock, set active_hours to null)" )
        return self

    @property
    def overnight( self ) -> bool:
        return self.end_hour < self.start_hour

    def contains( self, hour: int ) -> bool:
        """True when local `hour` (0-23) is inside the window. Pure."""
        if self.overnight:
            return hour >= self.start_hour or hour < self.end_hour
        return self.start_hour <= hour < self.end_hour


class PollSettings( BaseModel ):
    """Every number the poller uses. Defaults are the ones agreed on 2026-10-31."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    idle_seconds:         float = Field( default=30.0, ge=10.0 )     # nothing has happened for a while
    burst_seconds:        float = Field( default=5.0, ge=2.0 )       # just after a task, for follow-ups
    burst_window_seconds: float = Field( default=300.0, ge=0.0 )     # how long "just after" lasts
    slow_down_seconds:    float = Field( default=3600.0, ge=60.0 )   # the host said 429 / 503
    error_base_seconds:   float = Field( default=30.0, ge=5.0 )      # first wait after any other failure
    error_max_seconds:    float = Field( default=900.0, ge=60.0 )    # failures double up to this
    active_hours:         ActiveHours | None = None                  # None: around the clock


def load_settings( path: Path | None ) -> PollSettings:
    """`PollSettings` from a JSON file; the defaults when there is no file."""
    if path is None or not path.exists():
        return PollSettings()
    return PollSettings.model_validate( json.loads( path.read_text( encoding="utf-8" )))


class PollOutcome( str, Enum ):
    IDLE      = "idle"        # asked, nothing queued
    WORKED    = "worked"      # ran at least one task
    SLOW_DOWN = "slow_down"   # the host asked us to back off
    ERROR     = "error"       # anything else went wrong


class WorkerNotAuthorised( Exception ):
    """The gateway rejected the worker token. Polling again would only repeat it."""


def classify( error: Exception ) -> PollOutcome:
    """What a failure means for the schedule. A rejected token is not retried at all."""
    if isinstance( error, GatewayError ):
        if error.status in ( 401, 403 ):
            raise WorkerNotAuthorised( f"gateway answered { error.status } { error.code }" ) from None
        if error.status in ( 429, 503 ):
            return PollOutcome.SLOW_DOWN
    return PollOutcome.ERROR


class IPollSchedule( Protocol ):
    """Strategy: seconds to wait before the next check, given the last check's outcome."""

    def delay( self, outcome: PollOutcome, now: datetime ) -> float: ...


class BurstAfterWork:
    """Fast for a while after a task (follow-up questions), slow when idle."""

    def __init__( self, idle_seconds: float, burst_seconds: float, burst_window_seconds: float ) -> None:
        self._idle, self._burst = idle_seconds, burst_seconds
        self._window            = timedelta( seconds=burst_window_seconds )
        self._last_work: datetime | None = None

    def delay( self, outcome: PollOutcome, now: datetime ) -> float:
        if outcome is PollOutcome.WORKED:
            self._last_work = now
        if self._last_work is not None and now - self._last_work < self._window:
            return self._burst
        return self._idle


class ErrorBackoff:
    """Decorator: an hour after 429/503, doubling waits after other failures, untouched otherwise."""

    def __init__( self, inner: IPollSchedule, slow_down_seconds: float, base_seconds: float, max_seconds: float ) -> None:
        self._inner     = inner
        self._slow_down = slow_down_seconds
        self._base      = base_seconds
        self._max       = max_seconds
        self._failures  = 0

    def delay( self, outcome: PollOutcome, now: datetime ) -> float:
        normal = self._inner.delay( outcome, now )
        if outcome is PollOutcome.SLOW_DOWN:
            self._failures += 1
            return max( normal, self._slow_down )
        if outcome is PollOutcome.ERROR:
            self._failures += 1
            return max( normal, min( self._max, self._base * 2 ** ( self._failures - 1 )))
        self._failures = 0
        return normal


class ActiveHoursOnly:
    """Decorator: outside the window, sleep until it opens again."""

    def __init__( self, inner: IPollSchedule, hours: ActiveHours ) -> None:
        self._inner, self._hours = inner, hours

    def delay( self, outcome: PollOutcome, now: datetime ) -> float:
        normal = self._inner.delay( outcome, now )
        return max( normal, self.seconds_until_open( now ))

    def seconds_until_open( self, now: datetime ) -> float:
        if self._hours.contains( now.hour ):
            return 0.0
        opens = now.replace( hour=self._hours.start_hour, minute=0, second=0, microsecond=0 )
        if opens <= now:
            opens += timedelta( days=1 )
        return ( opens - now ).total_seconds()


def build_schedule( settings: PollSettings ) -> IPollSchedule:
    """Factory: the only place that picks and stacks the schedule pieces."""
    schedule: IPollSchedule = BurstAfterWork( settings.idle_seconds, settings.burst_seconds,
                                              settings.burst_window_seconds )
    schedule = ErrorBackoff( schedule, settings.slow_down_seconds, settings.error_base_seconds,
                             settings.error_max_seconds )
    if settings.active_hours is not None:
        schedule = ActiveHoursOnly( schedule, settings.active_hours )
    return schedule
