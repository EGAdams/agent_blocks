"""MemoryStore backed by a list. Used by tests, and as a --no-persist mode."""
import math

from ..models import EmbeddedTurn, MemoryQuery, RecalledTurn, Turn


def _cosine_distance( a: list[ float ], b: list[ float ]) -> float:
    dot = sum( x * y for x, y in zip( a, b ))
    norm_a = math.sqrt( sum( x * x for x in a ))
    norm_b = math.sqrt( sum( y * y for y in b ))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return 1.0 - ( dot / ( norm_a * norm_b ))


class InMemoryMemoryStore:
    def __init__( self ) -> None:
        self._rows: list[ EmbeddedTurn ] = []

    def add( self, turns: list[ EmbeddedTurn ]) -> None:
        self._rows.extend( turns )

    def search( self, query: MemoryQuery ) -> list[ RecalledTurn ]:
        candidates = [
            row for row in self._rows
            if query.session_id is None or row.turn.session_id == query.session_id
        ]
        scored = [
            RecalledTurn( turn=row.turn, distance=_cosine_distance( query.vector, row.vector ))
            for row in candidates
        ]
        scored.sort( key=lambda hit: hit.distance )
        return scored[ :query.limit ]

    def forget( self, ids: list[ str ]) -> int:
        """See MemoryStore.forget."""
        wanted = set( ids )
        before = len( self._rows )
        self._rows = [ row for row in self._rows if str( row.turn.id ) not in wanted ]
        return before - len( self._rows )

    def recent( self, session_id: str, limit: int ) -> list[ Turn ]:
        candidates = [ row.turn for row in self._rows if row.turn.session_id == session_id ]
        candidates.sort( key=lambda turn: turn.created_at, reverse=True )
        return candidates[ :limit ]
