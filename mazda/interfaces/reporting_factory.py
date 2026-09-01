"""Public contract for IReportingFactory. Known state: real default implementation."""

from typing import Protocol

from mazda.models.factory_handles import ReportHandle
from mazda.models.orchestration import OrchestrationResult


class IReportingFactory( Protocol ):
    """Builds events and trace/dashboard/regression reports."""

    def report( self, result: OrchestrationResult ) -> ReportHandle:
        """Produce the report for one orchestration trace."""
        ...
