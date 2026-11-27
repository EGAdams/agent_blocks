"""The Machine Supervisor's interfaces: one Protocol per responsibility. `kit.py` is the only module that
names the classes implementing them; operations see only these.

`Envelope` is agent_comms' handoff message -- Swarm-Forge's handoff file as a Pydantic model -- reused, not copied.
"""
from __future__ import annotations

from datetime import datetime
from typing import ContextManager, Protocol

from agent_comms.models import Envelope

from .models import (AgentReport, Alert, Check, CheckResult, Incident, Machine, Mail, MailReceipt, Policy, Ran,
                     WatchState)


class IMachineCatalog(Protocol):
    def me(self) -> Machine: ...
    def get(self, name: str) -> Machine: ...                      # KeyError for a machine not configured
    def by_agent(self, agent: str) -> Machine | None: ...
    def peers(self) -> list[Machine]: ...                         # of this machine, in wake order
    def policy(self) -> Policy: ...


class IProbe(Protocol):
    """One look at one check. Never raises: a probe that cannot run is a failed check with the reason."""
    def look(self, check: Check) -> CheckResult: ...


class IWatchState(Protocol):
    def load(self) -> WatchState: ...
    def save(self, state: WatchState) -> None: ...


class IIncidentStore(Protocol):
    def save(self, incident: Incident) -> None: ...
    def get(self, incident_id: str) -> Incident: ...              # KeyError when there is none


class ILock(Protocol):
    def hold(self, name: str) -> ContextManager[bool]: ...         # yields False when somebody else holds it
    def busy(self, name: str) -> bool: ...


class IClock(Protocol):
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...


class IRunLog(Protocol):
    def line(self, text: str) -> None: ...


class IWaker(Protocol):
    """Starts the sleeping supervisor for one incident and does not wait for it."""
    def wake(self, incident_id: str) -> None: ...


class INudge(Protocol):
    """Starts one fixed background job (the todo worker, the outbox flush) and does not wait for it."""
    def nudge(self) -> None: ...


class IRemoteAdw(Protocol):
    """Runs one of this package's ADWs on a machine: here as a child process, elsewhere over SSH. Never raises."""
    def run(self, machine: Machine, script: str, args: list[str], stdin: str | None, timeout: int) -> Ran: ...


class IAgentCli(Protocol):
    """One headless run of this machine's agent CLI (Claude or Codex), every shell command through the guard.
    Never raises."""
    def ask(self, prompt: str, rules: str, timeout: int, incident: str = "", look_only: bool = False,
            no_talk: bool = False) -> tuple[str, float | None, str]: ...     # (what it said, cost or None, error)

    def report(self, role: str, prompt: str, rules: str, incident: str = "",
               look_only: bool = False) -> AgentReport: ...


class ITodoAgent(Protocol):
    """The local supervisor agent doing one todo from its mailbox. Never raises."""
    def work(self, envelope: Envelope) -> AgentReport: ...


class IUsageLimit(Protocol):
    """Reads an agent's error: when it says the subscription's usage limit is used up, the time to try again.
    None for any other error. Never raises."""
    def until(self, said: str, now: datetime) -> datetime | None: ...


class IRepairAgent(Protocol):
    """A supervisor agent on one machine. Never raises: an agent that cannot be woken reports reached=False."""
    def attempt(self, incident: Incident, role: str) -> AgentReport: ...


class IAgentDirectory(Protocol):
    def on(self, machine: Machine) -> IRepairAgent: ...             # here, or on a peer over the handoff channel


class IAnswerer(Protocol):
    def answer(self, sender: str, question: str, incident: Incident | None) -> str: ...


class IHandoffBoxes(Protocol):
    """This machine's handoff mailboxes, Swarm-Forge's layout: outbox -> sent | failed, and
    inbox/new -> in_process -> completed | failed. A message's state is the box its file sits in."""
    def post(self, envelope: Envelope) -> None: ...                # into outbox
    def sent(self, envelope: Envelope) -> None: ...                # outbox -> sent
    def undelivered(self, envelope: Envelope, why: str) -> None: ...   # outbox -> failed
    def receive(self, envelope: Envelope) -> Envelope: ...         # into inbox/new
    def claim(self, envelope_id: str) -> Envelope | None: ...      # inbox/new -> in_process
    def release(self, envelope: Envelope) -> Envelope: ...         # in_process -> new (to be tried again)
    def requeue(self, envelope_id: str, why: str) -> Envelope | None: ...  # inbox/failed -> new, by name only
    def settle(self, envelope: Envelope, ok: bool) -> Envelope: ...    # in_process -> completed | failed
    def box(self, name: str) -> list[Envelope]: ...
    def find(self, envelope_id: str) -> tuple[str, Envelope] | None: ...   # (box name, envelope)


class ICarrier(Protocol):
    """Takes one handoff to another machine and brings its reply back (the handoff daemon's job, over SSH).
    Raises Undelivered when the machine cannot be reached or does not answer in time."""
    def carry(self, machine: Machine, envelope: Envelope, timeout: int) -> Envelope: ...


class IMessenger(Protocol):
    """Send one handoff to a peer's supervisor and wait for its reply. Raises Undelivered."""
    def send(self, to: Machine, task_type: str, text: str, context: dict, timeout: int,
             priority: int = 50, sender: str | None = None) -> Envelope: ...


class IHandoffHandler(Protocol):
    """What this machine does with one kind of handoff. Returns the reply's result; raises to fail it."""
    def handle(self, envelope: Envelope) -> str: ...


class IToast(Protocol):
    def show(self, alert: Alert) -> str: ...                       # one line; raises RuntimeError when not shown


class IAlerter(Protocol):
    def alert(self, alert: Alert) -> list[str]: ...                # one delivery line per machine


class IMailer(Protocol):
    def send(self, mail: Mail) -> MailReceipt: ...                 # never raises


class IOutbox(Protocol):
    def hold(self, mail: Mail) -> None: ...
    def pending(self) -> list[tuple[str, Mail]]: ...
    def drop(self, key: str) -> None: ...


class IBriefWriter(Protocol):
    """The words an agent is woken with. Prompts are files; this fills them in."""
    def repair(self, incident: Incident, role: str) -> str: ...
    def answer(self, sender: str, question: str, incident: Incident | None) -> str: ...
    def todo(self, envelope: Envelope) -> str: ...
    def rules(self) -> str: ...
