"""Public interface for coordinating the Mazda factories as one agent engine."""

from typing import Protocol

from mazda.models.agent_engine import AgentEngineSnapshot, WrapperRevision


class IAgentEngineVariant( Protocol ):
    """Tests, gates, activates, and rolls back wrapper revisions."""

    def snapshot( self ) -> AgentEngineSnapshot:
        """Capture the current state of every factory boundary."""
        ...

    def rollback( self, revision: WrapperRevision ) -> None:
        """Restore the agent engine to an earlier working wrapper version."""
        ...
