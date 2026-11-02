"""The one boundary that varies: where the list is kept. Protocols only."""
from __future__ import annotations

from typing import Protocol

from .models import Todo


class ITodoStore( Protocol ):
    """The company to-do list. Items are completed, never deleted."""

    def items( self ) -> list[ Todo ]: ...

    def add( self, text: str, by: str ) -> Todo: ...

    def complete( self, number: int, by: str ) -> Todo: ...
