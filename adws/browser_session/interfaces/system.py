"""The machine's clock, so waiting is testable without waiting."""
from __future__ import annotations

from typing import Protocol


class IClock( Protocol ):
    def now( self ) -> float: ...
    def sleep( self, seconds: float ) -> None: ...
