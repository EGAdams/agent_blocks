"""Public contract for Trainer's failure-supervision policy."""

from typing import Protocol

from mazda.models.escalation import CoachingVerdict, EscalationEvent


class ITrainerEscalationPolicy( Protocol ):
    """Decides when Trainer must intervene and grades Mazda's tool returns."""

    def should_escalate( self, event: EscalationEvent ) -> bool:
        """Return True only for an incomplete, failed, stalled, or missing callback."""
        ...

    def grade( self, event: EscalationEvent ) -> CoachingVerdict:
        """Grade a successful tool return and produce coaching feedback."""
        ...
