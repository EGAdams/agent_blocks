"""Public contract for IImprovementFactory. Known state: real default implementation."""

from typing import Protocol

from mazda.models.factory_handles import EvaluationVerdictHandle, ImprovementProposalHandle


class IImprovementFactory( Protocol ):
    """Proposes, experiments with, approves, snapshots, activates, and rolls back changes."""

    def propose( self, verdict: EvaluationVerdictHandle ) -> ImprovementProposalHandle:
        """Propose an improvement in response to a failed evaluation verdict."""
        ...
