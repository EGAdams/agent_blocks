"""Public contract for recording structured intake evidence and verdicts."""

from typing import Protocol

from mazda.models.intake import IntakeDocument, IntakeEvidence, IntakeVerdict


class IIntakeEvidenceRecorder( Protocol ):
    """Records the verdict and supporting evidence for one intake trace."""

    def record( self, document: IntakeDocument, verdict: IntakeVerdict ) -> IntakeEvidence:
        """Persist the verdict and evidence for one intake trace."""
        ...
