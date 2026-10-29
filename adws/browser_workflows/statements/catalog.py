"""IStatementCatalog: the bank a request names, by whole words."""
from __future__ import annotations

import re

from ..models import StatementSource


def _said( name: str, text: str ) -> bool:
    return re.search( rf"(?<![a-z0-9]){ re.escape( name.casefold())}(?![a-z0-9])", text.casefold()) is not None


class NameStatementCatalog:
    def __init__( self, sources: list[ StatementSource ]) -> None:
        self._sources = sources

    def find( self, text: str ) -> StatementSource | None:
        named = [ s for s in self._sources if any( _said( n, text ) for n in s.names )]
        return named[ 0 ] if len( named ) == 1 else None

    def sources( self ) -> list[ StatementSource ]:
        return list( self._sources )
