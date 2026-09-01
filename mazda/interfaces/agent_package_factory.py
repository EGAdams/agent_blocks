"""Public contract for IAgentPackageFactory. Known state: real default implementation."""

from typing import Protocol

from mazda.models.factory_handles import AgentPackageHandle


class IAgentPackageFactory( Protocol ):
    """Builds Mazda and other named agent packages."""

    def build_package( self, name: str ) -> AgentPackageHandle:
        """Return the named agent package."""
        ...
