"""The cron watch. No model, no mail of its own: it looks, counts, and wakes the supervisor when a check has
failed enough times in a row. Everything it wants said by mail goes into the outbox for the flush ADW.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from ..interfaces import (IClock, IHandoffBoxes, IIncidentStore, ILock, IMachineCatalog, INudge, IOutbox, IProbe,
                          IRunLog, IWaker, IWatchState)
from ..models import Check, CheckCounter, CheckResult, Incident, Unresolved, WatchOutcome, WatchState
from . import letters
from .todo import queued_todos

LOCK = "watch"
SUPERVISOR = "supervisor"


class Watch:
    def __init__(self, catalog: IMachineCatalog, probe: IProbe, state: IWatchState, incidents: IIncidentStore,
                 lock: ILock, clock: IClock, waker: IWaker, outbox: IOutbox, flush: INudge, todo_worker: INudge,
                 boxes: IHandoffBoxes, log: IRunLog) -> None:
        self._catalog, self._probe, self._state, self._incidents = catalog, probe, state, incidents
        self._lock, self._clock, self._waker, self._outbox = lock, clock, waker, outbox
        self._flush, self._todo_worker, self._boxes, self._log = flush, todo_worker, boxes, log

    def look(self) -> list[CheckResult]:
        return [self._probe.look(check) for check in self._catalog.me().checks]

    def run(self, dry_run: bool = False) -> WatchOutcome:
        if dry_run:
            return WatchOutcome(results=self.look(), lines=["dry run: nothing saved, nobody woken"])
        with self._lock.hold(LOCK) as mine:
            if not mine:
                return WatchOutcome(results=[], lines=["another watch run is still going; this one did nothing"])
            results = self.look()
            state, now = self._state.load(), self._clock.now()
            checks = {check.name: check for check in self._catalog.me().checks}
            lines, due, back = [], [], []
            for result in results:
                if result.ok:
                    state.counters.pop(result.check, None)
                    if state.unresolved.pop(result.check, None) is not None:
                        back.append(result.check)
                    continue
                counter = state.counters.setdefault(result.check, CheckCounter(since=now))
                counter.fails += 1
                held = state.unresolved.get(result.check)
                if held is not None and held.until > now:
                    lines.append(f"{result.check}: failing, left alone until {held.until:%F %H:%M}Z ({held.incident})")
                    continue
                state.unresolved.pop(result.check, None)
                if counter.fails >= checks[result.check].threshold:
                    due.append(result)
                else:
                    lines.append(f"{result.check}: failing {counter.fails}/{checks[result.check].threshold} ({result.detail})")
            for name in [n for n in (*state.counters, *state.unresolved)
                         if n not in checks and not n.startswith("cron-")]:
                state.counters.pop(name, None)           # a check taken out of the table
                state.unresolved.pop(name, None)
            if back:
                self._outbox.hold(letters.recovered(self._catalog.me(), back))
                state.outbox_tried = None
                lines.append(f"{', '.join(back)}: passing again")
            incident = None
            if due:
                incident, said = self._wake(state, [checks[r.check] for r in due], due, now)
                lines.append(said)
            self._nudges(state, now, woke=incident is not None, lines=lines)
            self._state.save(state)
            for line in lines:
                self._log.line(line)
            return WatchOutcome(results=results, incident=incident, lines=lines)

    def report(self, check: Check, failure: CheckResult) -> WatchOutcome:
        """A failure seen by something other than the watch (a wrapped cron job). Same wake rules, same lock."""
        for _ in range(10):
            with self._lock.hold(LOCK) as mine:
                if mine:
                    state, now = self._state.load(), self._clock.now()
                    held = state.unresolved.get(check.name)
                    if held is not None and held.until > now:
                        return WatchOutcome(results=[failure], lines=[f"{check.name}: failing, left alone until "
                                                                      f"{held.until:%F %H:%M}Z"])
                    incident, said = self._wake(state, [check], [failure], now)
                    self._state.save(state)
                    self._log.line(said)
                    return WatchOutcome(results=[failure], incident=incident, lines=[said])
            self._clock.sleep(1)
        return WatchOutcome(results=[failure], lines=[f"{check.name}: failing, but the watch lock stayed busy"])

    def _wake(self, state: WatchState, checks: list[Check], failures: list[CheckResult],
              now: datetime) -> tuple[str | None, str]:
        names = ", ".join(check.name for check in checks)
        policy, me = self._catalog.policy(), self._catalog.me()
        if self._lock.busy(SUPERVISOR):
            return None, f"{names}: failing; the supervisor is already awake, so it was not woken again"
        state.wakes = [at for at in state.wakes if at > now - timedelta(hours=24)]
        if len(state.wakes) >= policy.max_wakes_per_day:
            until = now + timedelta(minutes=policy.cooldown_minutes)
            for check in checks:
                state.unresolved[check.name] = Unresolved(incident="wake-limit", until=until)
            self._outbox.hold(letters.wake_limit(me, [c.name for c in checks], policy.max_wakes_per_day,
                                                 policy.cooldown_minutes / 60))
            state.outbox_tried = None
            return None, f"{names}: failing; wake limit of {policy.max_wakes_per_day} a day reached, mail held instead"
        incident = Incident(id=f"{now:%Y%m%dT%H%M%SZ}-{me.name.lower()}-{uuid.uuid4().hex[:6]}", machine=me.name,
                            opened_at=now, checks=checks, failures=failures)
        self._incidents.save(incident)
        state.wakes.append(now)
        self._waker.wake(incident.id)
        return incident.id, f"{names}: failing; supervisor woken for incident {incident.id}"

    def _nudges(self, state: WatchState, now: datetime, woke: bool, lines: list[str]) -> None:
        retry = timedelta(minutes=self._catalog.policy().mail_retry_minutes)
        if self._outbox.pending() and (state.outbox_tried is None or state.outbox_tried + retry <= now):
            state.outbox_tried = now
            self._flush.nudge()
            lines.append("held mail: trying to send it")
        # Not in a run that woke the supervisor: the todo worker could take the lock she needs first.
        if not woke and queued_todos(self._boxes) and not self._lock.busy(SUPERVISOR):
            self._todo_worker.nudge()
            lines.append("queued todos: worker started")
