"""RecallStrategy implementations -- the swappable 'which turns become context' policy."""
from .models import MemoryQuery, Turn
from .ports import MemoryStore, RecallStrategy


class SimilarityRecall:
    """Nearest-neighbour recall, returned in chronological order."""

    def __init__( self, store: MemoryStore, limit: int = 30, session_scoped: bool = True ) -> None:
        self._store = store
        self._limit = limit
        self._session_scoped = session_scoped

    def recall( self, session_id: str, query_vector: list[ float ]) -> list[ Turn ]:
        query = MemoryQuery(
            vector=query_vector,
            session_id=session_id if self._session_scoped else None,
            limit=self._limit,
        )
        hits = self._store.search( query )
        turns = [ hit.turn for hit in hits ]
        return sorted( turns, key=lambda t: t.created_at )


class TokenBudgetedRecall:
    """Decorator over another strategy: drops the OLDEST turns until it fits the budget.

    Guards the context window, which the original top-30 recall did not.
    """

    def __init__( self, inner: RecallStrategy, max_chars: int = 12000 ) -> None:
        self._inner = inner
        self._max_chars = max_chars

    def recall( self, session_id: str, query_vector: list[ float ]) -> list[ Turn ]:
        turns = self._inner.recall( session_id, query_vector )
        kept: list[ Turn ] = []
        used = 0
        for turn in reversed( turns ):           # newest first while trimming
            cost = len( turn.message ) + len( turn.speaker ) + 2
            if used + cost > self._max_chars:
                break
            kept.append( turn )
            used += cost
        return list( reversed( kept ))            # restore chronological order
