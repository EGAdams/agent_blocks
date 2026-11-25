"""The roles Failure Triage is written against. `kit.py` is the only module that names their concretes."""
from __future__ import annotations

from typing import Protocol

from .models import Cluster, Failure, OwnerRule


class IFailureSource(Protocol):
    """Read-only: failures recorded somewhere. Never moves, edits or deletes what it reads."""
    name: str

    def read(self) -> list[Failure]: ...


class IFingerprinter(Protocol):
    def normalise(self, error: str) -> str: ...

    def fingerprint(self, agent: str, error: str) -> str: ...


class IClusterLedger(Protocol):
    def load(self) -> dict[str, Cluster]: ...

    def save(self, clusters: dict[str, Cluster]) -> None: ...


class IOwnerTable(Protocol):
    def match(self, agent: str, normalised_error: str) -> OwnerRule: ...

    def rule(self, name: str) -> OwnerRule | None: ...


class ITriageRouter(Protocol):
    """Hands one cluster to its owner. Returns the post-office task id; raises RouteRefused on a refusal."""

    def send(self, cluster: Cluster, rule: OwnerRule) -> str: ...


class INotifier(Protocol):
    """Sends EG one digest. Returns the post-office task id; raises on failure."""

    def digest(self, subject: str, body: str) -> str: ...


class ICorrectionLog(Protocol):
    """Where a fix is written so the mechanic's next "Seen before" names it."""

    def record(self, cluster: Cluster, correction: str) -> str: ...


class ISecretCheck(Protocol):
    """The Bridge worker's SecretScanner: the kinds of secret a text seems to hold, or []."""

    def found(self, text: str) -> list[str]: ...


class RouteRefused(Exception):
    """The post office refused the request (unknown agent, not on the card, not accepted from this sender)."""
