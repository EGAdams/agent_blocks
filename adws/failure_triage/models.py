"""What crosses Failure Triage's boundaries. Pydantic only, no behaviour beyond small derived values."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClusterState = Literal["open", "routed", "fixed", "closed"]
Klass = Literal["code", "config", "environment", "caller", "person", "unmatched"]
ChangeKind = Literal["new", "grown", "reopened", "regressed", "closed"]


class Failure(BaseModel):
    """One failed task, as its failed mailbox envelope recorded it."""
    model_config = ConfigDict(frozen=True)
    task_id: str
    agent: str                      # the recipient whose run failed
    sender: str
    task_type: str
    error: str
    failed_at: datetime
    request: str = ""               # the first 200 characters of what was asked
    bridge_task_id: str = ""


class OwnerRule(BaseModel):
    """One row of owners.json: which failures it covers, and who gets them."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    agent: str = ".*"               # regex on Failure.agent
    error: str = ".*"               # regex on the normalised error
    klass: Klass
    owner: str                      # a post-office agent, "eg" (mail only) or "sender" (their alert board)
    task_type: str = ""             # what the owner is sent; empty for "eg" and "sender"
    advice: str = ""                # one line for EG's mail


class Cluster(BaseModel):
    """Every failure with one fingerprint, and where its fix stands."""
    model_config = ConfigDict(extra="forbid")
    id: str                         # the fingerprint
    agent: str
    sample_error: str
    variants: list[str] = Field(default_factory=list)       # distinct raw errors, up to 10
    sample_request: str = ""
    first_seen: datetime
    last_seen: datetime
    count: int = 0
    task_ids: list[str] = Field(default_factory=list)       # the newest 20
    klass: Klass = "unmatched"
    rule: str = ""
    owner: str = ""
    state: ClusterState = "open"
    routed_task: str = ""
    route_error: str = ""
    fix: str = ""
    fixed_at: datetime | None = None
    closed_at: datetime | None = None
    regressions: int = 0
    mailed_at: datetime | None = None
    mail_reason: str = ""           # set when a mail is owed and not yet sent

    def needs_owner(self) -> bool:
        return self.state == "open" and not self.routed_task and not self.route_error

    def is_live(self) -> bool:
        """Counts toward "zero": anything not closed."""
        return self.state != "closed"


class ClusterChange(BaseModel):
    model_config = ConfigDict(frozen=True)
    cluster: str
    kind: ChangeKind
    added: int = 0


class PassReport(BaseModel):
    """The outcome of one triage pass."""
    ok: bool
    line: str
    dry_run: bool = False
    read: int = 0
    unreadable: list[str] = Field(default_factory=list)
    changes: list[ClusterChange] = Field(default_factory=list)
    routed: dict[str, str] = Field(default_factory=dict)     # cluster -> routed task id, or "(dry run)"
    route_errors: dict[str, str] = Field(default_factory=dict)
    mailed: str = ""                                         # the mail task id
    counts: dict[str, int] = Field(default_factory=dict)     # clusters by state
