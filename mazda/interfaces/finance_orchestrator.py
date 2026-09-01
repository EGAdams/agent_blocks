"""Public contract for the finance orchestration workflow."""

from typing import Protocol

from mazda.models.intake import IntakeDocument
from mazda.models.orchestration import OrchestrationResult


class IFinanceOrchestrator( Protocol ):
    """Moves a document from scanner/PDF input to verified finance storage."""

    def process_document( self, document: IntakeDocument ) -> OrchestrationResult:
        """Classify, check repeats, categorize, and store one document."""
        ...
