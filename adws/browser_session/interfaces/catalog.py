"""Where the buttons worth pressing are looked up by name."""
from __future__ import annotations

from typing import Protocol

from ..models import ClickTarget


class ITargetCatalog( Protocol ):
    def get( self, name: str ) -> ClickTarget | None: ...
    def names( self ) -> list[ str ]: ...
