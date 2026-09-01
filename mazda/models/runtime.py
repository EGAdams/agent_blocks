"""Boundary models for the self-improvement control plane."""

from pydantic import BaseModel


class WrapperRevision( BaseModel ):
    """One versioned revision of the wrapper around the model."""

    revision_id: str
    activated: bool


class AgentEngineSnapshot( BaseModel ):
    """A point-in-time snapshot of every factory boundary in the agent engine family."""

    revision_id: str
