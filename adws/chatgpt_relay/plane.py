"""RelayPlane: the Facade the operations are injected with, and the factory that builds the real one.

An operation is handed one object and asks it for the five things it may need -- which instances exist,
how to speak to one, whether this message may be sent, how long to wait for an answer, and who to tell
while waiting. That keeps every operation's constructor one argument wide and keeps the decision about
*which* transport, *which* guards, *which* clock in exactly one place: `default_plane()`.

This module is the only one here allowed to name a concrete implementation, the same rule
`browser_control/plane.py` and `lancedb_memory/main.py` follow.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from .errors import RelayRefused
from .guards import GuardChain, default_guards
from .instances import DEFAULT_LABEL, StaticInstanceCatalog
from .interfaces import (IInstanceCatalog, IMessageGuard, IRelayClock, IRelayObserver, IRelayTransport,
                         ISettleFactory, ISettlePolicy)
from .observers import NoRelayObserver
from .settling import DEFAULT_POLL, DEFAULT_STABLE, DEFAULT_TIMEOUT, NewTurnSettle, NoSettle, StableTextSettle
from .transport import DEFAULT_TIMEOUT, HttpRelayTransport, ObservedTransport, RetryingTransport


class SystemRelayClock:
    """IRelayClock over the real clock. The one place the relay sleeps; a test injects a fake and a
    two-minute settle loop runs in no time at all."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


class SettleFactory:
    """ISettleFactory: the Strategy registry. `--settle stable_text|new_turn|none` reaches it and nothing
    else does, so adding a policy is one entry in `BUILDERS` and no operation changes."""

    def __init__(self, clock: IRelayClock, default: str = StableTextSettle.name) -> None:
        self._clock = clock
        self._default = default
        self._builders = {
            StableTextSettle.name: lambda timeout, stable: StableTextSettle(
                self._clock, timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
                stable_seconds=stable if stable is not None else DEFAULT_STABLE, poll_seconds=DEFAULT_POLL),
            NewTurnSettle.name: lambda timeout, stable: NewTurnSettle(
                self._clock, timeout=timeout if timeout is not None else 30.0),
            NoSettle.name: lambda timeout, stable: NoSettle(),
        }

    def names(self) -> list[str]:
        return list(self._builders)

    def build(self, name: str | None, timeout: float | None = None,
              stable_seconds: float | None = None) -> ISettlePolicy:
        key = (name or self._default).strip().lower()
        if key not in self._builders:
            raise RelayRefused(f"there is no {key!r} way of waiting for an answer. "
                               f"There is: {', '.join(self._builders)}.")
        return self._builders[key](timeout, stable_seconds)


@dataclass(frozen=True)
class RelayPlane:
    """Facade. Frozen, because an operation must not be able to reconfigure the relay it was given --
    a `--timeout` affects the policy it builds, never the plane the next operation sees."""

    catalog:  IInstanceCatalog
    transport: IRelayTransport
    guards:   IMessageGuard
    settles:  ISettleFactory
    clock:    IRelayClock
    observer: IRelayObserver

    def watched_by(self, observer: IRelayObserver) -> "RelayPlane":
        """A copy whose observer, and whose transport's observer, is this one. The ADW uses it to put the
        relay's own progress into the lifecycle log without the operations knowing there is a log."""
        return replace(self, observer=observer, transport=ObservedTransport(self.transport, observer))


def default_plane(timeout: float = DEFAULT_TIMEOUT, observer: IRelayObserver | None = None,
                  guards: GuardChain | None = None) -> RelayPlane:
    """Abstract Factory: the production relay. HTTP transport, one retry on a read, the standing guard
    chain, the real clock, and no observer unless one is given."""
    clock = SystemRelayClock()
    watcher = observer or NoRelayObserver()
    transport: IRelayTransport = RetryingTransport(HttpRelayTransport(timeout=timeout), clock)
    if observer is not None:
        transport = ObservedTransport(transport, watcher)
    return RelayPlane(catalog=StaticInstanceCatalog(), transport=transport, guards=guards or default_guards(),
                      settles=SettleFactory(clock), clock=clock, observer=watcher)


__all__ = ["RelayPlane", "SettleFactory", "SystemRelayClock", "default_plane", "DEFAULT_LABEL"]
