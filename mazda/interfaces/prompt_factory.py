"""Public contract for IPromptFactory. Known state: stub family, no implementation yet."""

from typing import Protocol

from mazda.models.factory_handles import PromptSetHandle


class IPromptFactory( Protocol ):
    """Composes and validates system messages and templates."""

    def build( self ) -> PromptSetHandle:
        """Return the composed and validated prompt set."""
        ...
