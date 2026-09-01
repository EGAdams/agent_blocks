"""Public contract for IContextFactory. Known state: stub family, no implementation yet."""

from typing import Protocol

from mazda.models.factory_handles import ContextBundleHandle


class IContextFactory( Protocol ):
    """Selects context, budgets, memory/notes, and detects staleness."""

    def build_context( self ) -> ContextBundleHandle:
        """Return the selected context bundle for the current run."""
        ...
