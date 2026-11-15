"""The ladder: this machine's supervisor, then each peer's in turn, then a high-priority mail with a prompt for
a more capable agent. Whether an incident is fixed is decided only by running its checks again from here.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from ..interfaces import (IAgentDirectory, IAlerter, IClock, IIncidentStore, ILock, IMachineCatalog, IMailer, IProbe,
                          IRunLog, IWatchState)
from ..models import Alert, Attempt, Check, CheckResult, Incident, Mail, Unresolved
from . import letters

LOCK = "supervisor"
WATCH = "watch"


class Escalation:
    def __init__(self, catalog: IMachineCatalog, probe: IProbe, incidents: IIncidentStore, agents: IAgentDirectory,
                 alerter: IAlerter, mailer: IMailer, state: IWatchState, lock: ILock, clock: IClock,
                 log: IRunLog) -> None:
        self._catalog, self._probe, self._incidents, self._agents = catalog, probe, incidents, agents
        self._alerter, self._mailer, self._state = alerter, mailer, state
        self._lock, self._clock, self._log = lock, clock, log

    def drill(self) -> Incident | None:
        """A made-up incident nobody can fix, so every rung, alert and mail runs. Agents change nothing."""
        me, now = self._catalog.me(), self._clock.now()
        check = Check(name="drill", kind="command", argv=["false"], threshold=1,
                      about="A drill. This check always fails. Look around, change nothing, and report.")
        incident = Incident(id=f"{now:%Y%m%dT%H%M%SZ}-{me.name.lower()}-drill{uuid.uuid4().hex[:4]}", machine=me.name,
                            opened_at=now, checks=[check], drill=True,
                            failures=[CheckResult(check="drill", ok=False, detail="exit 1: drill")])
        self._incidents.save(incident)
        return self.run(incident.id)

    def run(self, incident_id: str) -> Incident | None:
        with self._lock.hold(LOCK) as mine:
            if not mine:
                self._log.line(f"incident {incident_id}: another supervisor run holds the lock; left open")
                return None
            incident = self._incidents.get(incident_id)
            if incident.status != "open":
                self._log.line(f"incident {incident_id}: already {incident.status}")
                return incident
            return self._climb(incident)

    def _climb(self, incident: Incident) -> Incident:
        me = self._catalog.me()
        names = ", ".join(f.check for f in incident.failures)
        tag = "[DRILL] " if incident.drill else ""
        self._log.line(f"incident {incident.id}: {names}")
        if not incident.drill and all(r.ok for r in self._look(incident)):
            return self._close(incident, "fixed", letters.fixed(incident))
        alerted = False
        for rung, machine in enumerate([me, *self._catalog.peers()], start=1):
            if rung > 1:
                before = incident.attempts[-1].machine
                self._say(Alert(title=f"{tag}{me.name}: {names} is failing"[:120],
                                message=f"{before}'s supervisor could not fix it. Waking {machine.name}'s supervisor."))
                alerted = True
            self._log.line(f"incident {incident.id}: rung {rung}, {machine.name}'s supervisor")
            report = self._agents.on(machine).attempt(incident, "first" if rung == 1 else "helper")
            recheck = self._recheck(incident)
            fixed = all(r.ok for r in recheck)
            incident.attempts.append(Attempt(rung=rung, machine=machine.name, report=report, recheck=recheck,
                                             fixed=fixed, at=self._clock.now()))
            self._incidents.save(incident)
            self._log.line(f"incident {incident.id}: rung {rung} {'FIXED' if fixed else 'not fixed'} -- "
                           f"{(report.diagnosis or report.error)[:300]}")
            if fixed:
                if alerted:
                    self._say(Alert(title=f"{tag}{me.name}: {names} is fixed"[:120],
                                    message=f"{machine.name}'s supervisor fixed it. A mail has the details."))
                return self._close(incident, "fixed", letters.fixed(incident))
        self._say(Alert(title=f"{tag}{me.name}: {names} NOT fixed"[:120],
                        message="All three supervisors tried and could not fix it. EG has a high-priority mail "
                                "with a prompt for a more capable agent."))
        if not incident.drill:
            self._cool_down(incident)
        machines = [me, *self._catalog.peers()]
        return self._close(incident, "unresolved", letters.unresolved(incident, me, incident.checks, machines))

    def _look(self, incident: Incident) -> list[CheckResult]:
        return [self._probe.look(check) for check in incident.checks]

    def _recheck(self, incident: Incident) -> list[CheckResult]:
        policy, results = self._catalog.policy(), []
        for _ in range(max(1, policy.recheck_tries)):
            self._clock.sleep(policy.settle_seconds)
            results = self._look(incident)
            if all(r.ok for r in results):
                break
        return results

    def _say(self, alert: Alert) -> None:
        for line in self._alerter.alert(alert):
            self._log.line(f"alert '{alert.title}' -> {line}")

    def _close(self, incident: Incident, status: str, mail: Mail) -> Incident:
        incident.status, incident.closed_at = status, self._clock.now()
        self._incidents.save(incident)
        receipt = self._mailer.send(mail)
        self._log.line(f"incident {incident.id}: {status}; mail '{mail.subject}' "
                       f"{'sent via ' + receipt.via if receipt.sent else 'NOT sent (' + receipt.via + ': ' + receipt.detail + ')'}")
        return incident

    def _cool_down(self, incident: Incident) -> None:
        until = self._clock.now() + timedelta(minutes=self._catalog.policy().cooldown_minutes)
        for _ in range(10):
            with self._lock.hold(WATCH) as mine:
                if mine:
                    state = self._state.load()
                    for failure in incident.failures:
                        state.unresolved[failure.check] = Unresolved(incident=incident.id, until=until)
                    self._state.save(state)
                    return
            self._clock.sleep(1)
        self._log.line(f"incident {incident.id}: the watch lock stayed busy; no cooldown written")
