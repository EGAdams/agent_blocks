"""Boundary models for the live intake track (Agent -> Orchestrator -> Evidence)."""

from pydantic import BaseModel


class IntakeDocument( BaseModel ):
    """A scanned or PDF finance document entering the intake pipeline."""

    document_id: str
    source_path: str


class IntakeVerdict( BaseModel ):
    """The classify/repeat/categorize/store verdict reached for one document."""

    document_id: str
    verdict: str


class IntakeEvidence( BaseModel ):
    """Structured evidence recorded for one intake trace."""

    document_id: str
    trace_id: str
