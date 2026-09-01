"""Public contract for IToolFactory. Known state: stub family, no implementation yet."""

from typing import Protocol

from mazda.models.factory_handles import ToolRegistryHandle


class IToolFactory( Protocol ):
    """Builds the tool registry, adapter, permissions, and execution logging."""

    def registry( self ) -> ToolRegistryHandle:
        """Return the configured tool registry."""
        ...
