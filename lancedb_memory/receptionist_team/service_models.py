"""Pydantic contracts for the Toyota sidecar's HTTP boundary.

These shapes cross a process boundary (browser -> dashboard -> this service),
which is exactly where the workspace requires a BaseModel instead of an
ad-hoc dict. Kept in their own module so service.py stays about wiring and
the dashboard can read the contract without importing the service.

The sidecar serves exactly one session -- the warm agent's -- so a turn
request carries no session id. The terminal record reports which session
answered, which is what the browser stores as its conversation id.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Generous enough for a dictated paragraph, small enough that a runaway
# client can't make us buffer a whole prompt's worth of memory.
MAX_TURN_CHARS = 8000

_SAFE_SESSION_RE = re.compile(r"[A-Za-z0-9._-]{1,120}")


class TurnRequest(BaseModel):
    """One untrusted turn of user speech or typing."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TURN_CHARS)
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,100}$")

    @field_validator("text")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        if not value.strip():
            raise ValueError("message is empty")
        return value


class TurnRecord(BaseModel):
    """One NDJSON line of a turn's response.

    kind=acknowledgment  spoken immediately, before a slow delegation starts
    kind=reply           Toyota's answer
    kind=delta           incremental public text; controls remain private
    kind=result          terminal; no more records for this turn
    kind=error           terminal; the turn failed and said why
    kind=work            an installed workflow operation for the browser
    kind=handoff         open another agent's window (see adapters/handoff_model.py)

    A reply with streamed=True is the complete persisted snapshot. Consumers
    that displayed delta records must not append that snapshot a second time.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["acknowledgment", "delta", "reply", "result", "error", "work", "handoff"]
    text: str = ""
    streamed: bool = False
    cost_usd: float | None = None
    session: str | None = None
    work: dict | None = None
    handoff: dict | None = None

    @field_validator("session")
    @classmethod
    def safe_session(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_SESSION_RE.fullmatch(value):
            raise ValueError("unsafe session id")
        return value

    @property
    def is_terminal(self) -> bool:
        return self.kind in ("result", "error")


# Sessions the shared-memory endpoints may read and write. Toyota's own
# session is written only by her turns, never through these endpoints.
MemorySession = Literal["mazda"]


class MemoryComposeRequest(BaseModel):
    """Ask for recalled context to put in front of one of another agent's turns."""

    model_config = ConfigDict(extra="forbid")

    session: MemorySession
    text: str = Field(min_length=1, max_length=MAX_TURN_CHARS)


class MemoryRecordRequest(BaseModel):
    """Persist one finished exchange of another agent into the shared store."""

    model_config = ConfigDict(extra="forbid")

    session: MemorySession
    user: str = Field(min_length=1, max_length=MAX_TURN_CHARS)
    agent: str = Field(default="", max_length=MAX_TURN_CHARS * 4)
