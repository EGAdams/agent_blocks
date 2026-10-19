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
    kind=result          terminal; no more records for this turn
    kind=error           terminal; the turn failed and said why
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["acknowledgment", "reply", "result", "error", "work"]
    text: str = ""
    cost_usd: float | None = None
    session: str | None = None
    work: dict | None = None

    @field_validator("session")
    @classmethod
    def safe_session(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_SESSION_RE.fullmatch(value):
            raise ValueError("unsafe session id")
        return value

    @property
    def is_terminal(self) -> bool:
        return self.kind in ("result", "error")
