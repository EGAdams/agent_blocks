"""Public interface for the agent engine that manages model wrapper versions."""

from typing import Protocol

from mazda.models.agent_engine import WrapperRevision


class IAgentEngine( Protocol ):
    """Manages which model wrapper version is currently active."""

    def active_revision( self ) -> WrapperRevision:
        """Return the model wrapper version currently being used."""
        ...