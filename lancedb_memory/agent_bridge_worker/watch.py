"""PollLoop: keep checking the gateway, and run the worker ADW only when there is work.

An idle check is one `GET /worker/tasks/next` and nothing else -- no post office,
no SSSF run directory, no log line. The full ADW (claim through finalize) runs
only after a check finds a task. How long to wait between checks is entirely
the injected `IPollSchedule`'s call.

Observer: after every check (and when a task starts) the loop tells its
`IPollObserver`s what it is doing and when it will check next. The dashboard's
Poll Settings tab reads that through `adapters/poll_state_file.py`; the loop
neither knows nor cares who is listening.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from .polling import IPollSchedule, PollOutcome, WorkerNotAuthorised, classify


class PollState( BaseModel ):
    """What the poller is doing right now. Times are local ISO-8601 with offset."""

    model_config = ConfigDict( frozen=True )

    phase:         Literal[ "waiting", "working", "stopped" ]
    outcome:       PollOutcome | None = None     # the last check's outcome
    checked_at:    datetime | None = None
    delay_seconds: float | None = None           # length of the current wait
    next_check_at: datetime | None = None
    checks:        int = 0
    pid:           int | None = None


class IPollObserver( Protocol ):
    def changed( self, state: PollState ) -> None: ...


class IWaiter( Protocol ):
    """Sleeps, but wakes early when asked to stop (SIGTERM from systemd)."""

    def wait( self, seconds: float ) -> bool: ...     # True: stop was requested

    @property
    def stopping( self ) -> bool: ...


class PollLoop:
    def __init__( self, has_work: Callable[[], bool ], run_work: Callable[[], int ], schedule: IPollSchedule,
                  waiter: IWaiter, now: Callable[[], datetime ], say: Callable[[ str ], None ],
                  observers: Sequence[ IPollObserver ] = ()) -> None:
        self._has_work  = has_work
        self._run_work  = run_work
        self._schedule  = schedule
        self._waiter    = waiter
        self._now       = now
        self._say       = say
        self._observers = list( observers )
        self._last: PollOutcome | None = None
        self.checks     = 0

    def run( self ) -> None:
        """Until stopped. `WorkerNotAuthorised` propagates: a bad token must stop the service."""
        try:
            while not self._waiter.stopping:
                outcome = self.tick()
                now     = self._now()
                delay   = self._schedule.delay( outcome, now )
                if outcome is not self._last or outcome is PollOutcome.WORKED:
                    self._say( f"{ outcome.value }; next check in { delay:.0f}s" )
                self._last = outcome
                self._tell( PollState( phase="waiting", outcome=outcome, checked_at=now, delay_seconds=delay,
                                       next_check_at=now + timedelta( seconds=delay ), checks=self.checks ))
                if self._waiter.wait( delay ):
                    break
        finally:
            self._tell( PollState( phase="stopped", outcome=self._last, checked_at=self._now(), checks=self.checks ))

    def tick( self ) -> PollOutcome:
        self.checks += 1
        try:
            if not self._has_work():
                return PollOutcome.IDLE
            self._tell( PollState( phase="working", outcome=self._last, checked_at=self._now(), checks=self.checks ))
            done = self._run_work()
            return PollOutcome.WORKED if done else PollOutcome.IDLE
        except WorkerNotAuthorised:
            raise
        except Exception as error:  # noqa: BLE001 -- one bad check or task must not end the service
            outcome = classify( error )
            self._say( f"check failed: { type( error ).__name__ }: { str( error )[ :200 ]}" )
            return outcome

    def _tell( self, state: PollState ) -> None:
        for observer in self._observers:
            try:
                observer.changed( state )
            except Exception as error:  # noqa: BLE001 -- a broken display must never stop the worker
                self._say( f"observer failed: { type( error ).__name__ }: { error }" )
