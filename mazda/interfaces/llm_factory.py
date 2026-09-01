"""Public contract for ILlmFactory. Known state: stub family, no implementation yet."""

from typing import Protocol

from mazda.models.factory_handles import LlmClientHandle


class ILlmFactory( Protocol ):
    """Builds the model client and its token and budget services."""

    def client( self ) -> LlmClientHandle:
        """Return the configured client for request/response handling."""
        ...
