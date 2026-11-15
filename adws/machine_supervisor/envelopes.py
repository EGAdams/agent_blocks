"""Building handoff envelopes and naming their files. The id sorts by time and names the sender."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from agent_comms.models import Envelope, MessageKind, TaskStatus

ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}_from_[a-z][a-z0-9-]*$")
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
INBOX = ("new", "in_process", "completed", "failed")
BOXES = (*INBOX, "outbox", "sent", "undelivered")


def last_json_line(text: str) -> str | None:
    """An ADW's answer is the last line of its output that is a JSON object; anything above it is its log."""
    return next((line for line in reversed(text.splitlines()) if line.startswith("{")), None)


def new_id(sender: str, now: datetime) -> str:
    return f"{now:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}_from_{sender}"


def request(sender: str, recipient: str, task_type: str, text: str, context: dict, priority: int,
            now: datetime) -> Envelope:
    ident = new_id(sender, now)
    return Envelope(id=ident, kind=MessageKind.REQUEST, sender=sender, recipient=recipient, task_type=task_type,
                    text=text, context=context, priority=priority, correlation_id=ident, trace=[sender],
                    created_at=now)


def reply_to(asked: Envelope, performer: str, outcome: TaskStatus, result: str | None, error: str | None,
             now: datetime) -> Envelope:
    return Envelope(id=new_id(performer, now), kind=MessageKind.REPLY, sender=performer, recipient=asked.sender,
                    task_type=asked.task_type, text=result or error or "", priority=asked.priority,
                    correlation_id=asked.id, parent_id=asked.id, trace=[*asked.trace, performer],
                    outcome=outcome, result=result, error=error, performer=performer, created_at=now)


def filename(envelope: Envelope) -> str:
    return f"{envelope.priority:02d}_{envelope.id}_to_{envelope.recipient}.json"
