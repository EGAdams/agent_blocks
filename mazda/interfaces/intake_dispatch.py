"""Public contract for routing intake documents to the finance orchestrator."""

from typing import Protocol

from mazda.models.intake import IntakeDocument
from mazda.models.orchestration import DashboardCallback


class IIntakeDispatcher( Protocol ):
    """Hands a document to the orchestrator and always notifies the dashboard."""

    def dispatch( self, document: IntakeDocument ) -> DashboardCallback:
        """Dispatch one document and return the callback sent to the dashboard."""
        ...
