"""WordBankDirectory: IBankDirectory over a list of BankProfiles."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..models import BankProfile, GoalVocabulary
from .profiles import COMMON_GOALS


class WordBankDirectory:
    def __init__( self, banks: list[ BankProfile ]) -> None:
        self._banks = banks

    def find( self, text: str ) -> BankProfile | None:
        said = f" { re.sub( r'[^a-z0-9/]+', ' ', text.casefold()) } "
        best: tuple[ int, BankProfile ] | None = None
        for bank in self._banks:
            for name in bank.names:
                if f" { name.casefold() } " in said and ( best is None or len( name ) > best[ 0 ]):
                    best = ( len( name ), bank )
        return best[ 1 ] if best else None

    def by_key( self, key: str ) -> BankProfile | None:
        return next(( b for b in self._banks if b.key == key ), None )

    def all( self ) -> list[ BankProfile ]:
        return list( self._banks )

    def for_url( self, url: str ) -> BankProfile | None:
        host = ( urlparse( url ).hostname or "" ).casefold()
        return next(( b for b in self._banks if any( host == h or host.endswith( "." + h ) for h in b.hosts )), None )


def goal_vocabulary( bank: BankProfile | None, goal: str ) -> GoalVocabulary | None:
    """The bank's own words for a goal, else everyone's."""
    if bank is not None and goal in bank.goals:
        return bank.goals[ goal ]
    return COMMON_GOALS.get( goal )
