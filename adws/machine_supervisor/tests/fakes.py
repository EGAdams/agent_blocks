"""Fakes for the offline suite: no network, no model, no SSH, no clock. Real files only under tmp_path."""
from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from machine_supervisor.adapters.catalog import JsonMachineCatalog
from machine_supervisor.adapters.handoffs import FileHandoffBoxes
from machine_supervisor.adapters.stores import FileIncidentStore, FileOutbox, FileWatchState
from machine_supervisor.errors import Undelivered
from machine_supervisor.models import AgentReport, CheckResult, MailReceipt

TABLE = {
    "policy": {"settle_seconds": 0, "recheck_tries": 1, "max_wakes_per_day": 3, "cooldown_minutes": 60},
    "machines": [
        {"name": name, "hostname": f"host-{name.lower()}", "ssh": {"host": f"10.0.0.{n}", "port": 2222},
         "python": "/py", "repo": "/repo", "claude": "/claude", "peers": peers, "always_on": name != "R46",
         "notes": [f"note about {name}"],
         "checks": [{"name": "web", "kind": "http", "target": "http://localhost:1/", "about": "the web thing"},
                    {"name": "unit", "kind": "unit", "target": "x.service", "threshold": 1}]}
        for n, (name, peers) in enumerate([("BATI", ["SQMC", "R46"]), ("SQMC", ["BATI", "R46"]),
                                           ("R46", ["BATI", "SQMC"])], start=1)],
}


def catalog(tmp: Path, me: str = "BATI") -> JsonMachineCatalog:
    path = tmp / "machines.json"
    path.write_text(json.dumps(TABLE))
    return JsonMachineCatalog(path, me)


class FakeClock:
    def __init__(self) -> None:
        self.at = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        self.at += timedelta(seconds=1)
        return self.at

    def sleep(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class FakeProbe:
    """Checks named in `failing` fail. `script`, when set, is popped once per look() round of the first check."""
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = set(failing or ())

    def look(self, check) -> CheckResult:
        bad = check.name in self.failing
        return CheckResult(check=check.name, ok=not bad, detail="down" if bad else "up")


class FakeLock:
    def __init__(self) -> None:
        self.taken: set[str] = set()

    @contextlib.contextmanager
    def hold(self, name: str):
        if name in self.taken:
            yield False
            return
        self.taken.add(name)
        try:
            yield True
        finally:
            self.taken.discard(name)

    def busy(self, name: str) -> bool:
        return name in self.taken


class Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def line(self, text: str) -> None:
        self.lines.append(text)


class Counter:
    """IWaker and INudge."""
    def __init__(self) -> None:
        self.woken: list[str] = []
        self.nudges = 0

    def wake(self, incident_id: str) -> None:
        self.woken.append(incident_id)

    def nudge(self) -> None:
        self.nudges += 1


class FakeAgent:
    """IRepairAgent / ITodoAgent / IAnswerer. `fixes` is called when it attempts, so a test can heal the probe."""
    def __init__(self, machine: str, fixes=None, reached: bool = True) -> None:
        self.machine, self.fixes, self.reached, self.seen = machine, fixes, reached, []

    def attempt(self, incident, role: str) -> AgentReport:
        self.seen.append((incident.id, role))
        if not self.reached:
            return AgentReport(machine=self.machine, role=role, reached=False, error="ssh: no route")
        if self.fixes:
            self.fixes()
        return AgentReport(machine=self.machine, role=role, diagnosis=f"{self.machine} looked",
                           actions=["restarted x"] if self.fixes else [], believes_fixed=bool(self.fixes))

    def work(self, envelope) -> AgentReport:
        self.seen.append((envelope.id, "todo"))
        return AgentReport(machine=self.machine, role="todo", diagnosis=f"did: {envelope.text}")

    def answer(self, sender: str, question: str, incident) -> str:
        self.seen.append((sender, question, incident.id if incident else None))
        return f"{self.machine} says 42"


class Directory:
    def __init__(self, agents: dict) -> None:
        self.agents = agents

    def on(self, machine):
        return self.agents[machine.name]


class FakeAlerter:
    def __init__(self) -> None:
        self.alerts = []

    def alert(self, alert) -> list[str]:
        self.alerts.append(alert)
        return ["BATI: shown"]


class FakeMailer:
    def __init__(self, sends: bool = True, via: str = "fake") -> None:
        self.sends, self.via, self.mails = sends, via, []

    def send(self, mail) -> MailReceipt:
        self.mails.append(mail)
        return MailReceipt(sent=self.sends, via=self.via, detail="" if self.sends else "connector down")


class FakeToast:
    def __init__(self, works: bool = True) -> None:
        self.works, self.shown = works, []

    def show(self, alert) -> str:
        if not self.works:
            raise RuntimeError("no interop")
        self.shown.append(alert)
        return "shown"


class LoopCarrier:
    """ICarrier that hands the envelope straight to another machine's Receiver in this process."""
    def __init__(self) -> None:
        self.receivers: dict = {}
        self.down: set[str] = set()

    def carry(self, machine, envelope, timeout: int):
        if machine.name in self.down or machine.name not in self.receivers:
            raise Undelivered(f"{machine.name}: connection refused")
        return self.receivers[machine.name].receive(envelope.model_dump_json())


class Stores:
    def __init__(self, tmp: Path) -> None:
        self.state = FileWatchState(tmp / "state.json")
        self.incidents = FileIncidentStore(tmp / "incidents")
        self.outbox = FileOutbox(tmp / "outbox")
        self.boxes = FileHandoffBoxes(tmp / "handoffs")
