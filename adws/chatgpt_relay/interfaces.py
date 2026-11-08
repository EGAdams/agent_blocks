"""What the relay's operations need from the machine, as Protocols (say "interface", never "port").

One interface per responsibility, so each concrete has exactly one reason to change:

    IRelayTransport   speak to one browser server              transport.py (and its Decorators)
    IInstanceCatalog  which ChatGPT sessions exist             instances.py
    IMessageGuard     may this message be sent at all          guards.py       (Chain of Responsibility)
    ISettlePolicy     is the answer finished                   settling.py     (Strategy)
    ISettleFactory    build the policy this call asked for     plane.py        (Abstract Factory)
    IRelayObserver    say what is happening while we wait      observers.py    (Observer + Null Object)
    IRelayClock       time, so a test does not sleep           plane.py
"""
from __future__ import annotations

from typing import Any, Protocol

from .models import GuardVerdict, RelayInstance, SettleReport, ThreadSnapshot, ThreadView


class IRelayTransport(Protocol):
    """One ChatGPT browser session's HTTP surface. Never raises for a refusal; raises RelayUnreachable
    only when the server could not be spoken to at all.

    There is deliberately **no** `quit`. Quitting that Chrome discards the signed-in profile, which a
    person then has to restore by hand, and no relay operation has a reason to -- so the capability is
    absent from the interface rather than present and guarded.
    """
    def health(self, instance: RelayInstance) -> dict[str, Any]: ...
    def open(self, instance: RelayInstance, url: str) -> dict[str, Any]: ...
    def new_chat(self, instance: RelayInstance) -> dict[str, Any]: ...
    def type(self, instance: RelayInstance, text: str) -> dict[str, Any]: ...
    def send(self, instance: RelayInstance) -> dict[str, Any]: ...
    def read_thread(self, instance: RelayInstance, last: int | None = None) -> ThreadView: ...


class IInstanceCatalog(Protocol):
    def labels(self) -> list[str]: ...
    def all(self) -> list[RelayInstance]: ...
    def resolve(self, name: str | None) -> RelayInstance: ...     # raises InstanceUnknown


class IMessageGuard(Protocol):
    """One reason a message may not be sent. A link in the chain: it answers for itself only."""
    @property
    def name(self) -> str: ...

    def check(self, message: str, args: dict[str, Any], instance: RelayInstance) -> GuardVerdict: ...


class ISettlePolicy(Protocol):
    """How we decide ChatGPT has finished answering. ChatGPT streams, so one read is never enough."""
    @property
    def name(self) -> str: ...

    def settle(self, instance: RelayInstance, transport: IRelayTransport, before: ThreadSnapshot,
               observer: "IRelayObserver") -> SettleReport: ...


class ISettleFactory(Protocol):
    """Abstract Factory over the settle Strategies: a caller names a policy and its patience, and gets a
    built policy back. It exists so an operation never imports `settling` -- the policy a `--settle`
    flag selects is decided in one place, and a fourth policy is one registry entry."""
    def names(self) -> list[str]: ...

    def build(self, name: str | None, timeout: float | None = None,
              stable_seconds: float | None = None) -> ISettlePolicy: ...


class IRelayObserver(Protocol):
    """Told what is happening while a settle loop runs, so a long wait is not a silent one."""
    def waiting(self, polls: int, waited: float, note: str) -> None: ...

    def settled(self, report: SettleReport) -> None: ...


class IRelayClock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
