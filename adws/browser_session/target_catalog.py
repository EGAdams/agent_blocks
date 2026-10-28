"""ITargetCatalog over an in-memory list of ClickTargets."""
from __future__ import annotations

from .models import ClickTarget


class InMemoryTargetCatalog:

    def __init__( self, targets: list[ ClickTarget ]):
        self._targets = { t.name: t for t in targets }

    def get( self, name: str ) -> ClickTarget | None:
        return self._targets.get( name )

    def names( self ) -> list[ str ]:
        return sorted( self._targets )
