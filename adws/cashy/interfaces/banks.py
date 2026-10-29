from __future__ import annotations

from typing import Protocol

from ..models import BankProfile


class IBankDirectory( Protocol ):
    """Which bank a sentence names. Data in, never a hard-coded branch."""

    def find( self, text: str ) -> BankProfile | None: ...

    def by_key( self, key: str ) -> BankProfile | None: ...

    def all( self ) -> list[ BankProfile ]: ...

    def for_url( self, url: str ) -> BankProfile | None: ...
