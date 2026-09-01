"""Public contract for IEvaluationFactory. Known state: real finance implementation."""

from typing import Protocol

from mazda.models.factory_handles import EvaluationVerdictHandle
from mazda.models.orchestration import OrchestrationResult


class IEvaluationFactory( Protocol ):
    """Evaluates a trace and produces a verdict, gates, and failure classification."""

    def evaluate( self, result: OrchestrationResult ) -> EvaluationVerdictHandle:
        """Judge one orchestration trace using the finance verifiers."""
        ...
