"""Values that cross a boundary. Data only."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LiveEvent(BaseModel):
    """One recorded thing. Empty means the source did not record it; nothing is filled in."""
    at: str                          # ISO 8601, UTC
    machine: str
    agent: str
    kind: str                        # the source's own event or record name
    state: str = ""
    message: str = ""
    task_id: str = ""                # post-office envelope id (mail correlation id)
    bridge_task_id: str = ""
    request_id: str = ""
    adw_id: str = ""
    source: str = ""                 # the file the line came from


class Activity(BaseModel):
    ok: bool = True
    error: str = ""
    machine: str
    server_now: str
    cursor: int = 0                  # byte offset into the post-office event log
    agent_events: list[LiveEvent] = Field(default_factory=list)
    supervisor_events: list[LiveEvent] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)   # what this machine could not show, in plain words
