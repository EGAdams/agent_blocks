"""Public contract for IWorkflowFactory. Known state: stub family, no implementation yet."""

from typing import Protocol

from mazda.models.factory_handles import WorkflowRunHandle


class IWorkflowFactory( Protocol ):
    """Builds the workflow runner."""

    def runner( self ) -> WorkflowRunHandle:
        """Return the configured workflow runner."""
        ...
