"""Boundary models for Trainer's failure-supervision track."""

from pydantic import BaseModel


class EscalationEvent( BaseModel ):
    """A tool-return event Trainer may need to grade or escalate on."""

    trace_id: str
    callback_received: bool


class CoachingVerdict( BaseModel ):
    """Trainer's grading and coaching output for one escalation event."""

    trace_id: str
    verdict: str
