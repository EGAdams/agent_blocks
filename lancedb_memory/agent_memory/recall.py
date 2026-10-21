"""RecallStrategy implementations -- the swappable 'which turns become context' policy."""
from collections.abc import Callable

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


class RecencyFloorRecall:
    """Decorator over another strategy: guarantees the most recent N turns.

    SimilarityRecall alone is pure nearest-neighbour -- in a long-lived
    session, old turns that happen to share wording with the current message
    can fill every slot and crowd out the turns that are actually recent and
    relevant. This unions the inner strategy's hits with a hard recency
    floor from the store, so "what did we just say" always has an answer.
    """

    def __init__( self, inner: RecallStrategy, store: MemoryStore, floor: int = 10 ) -> None:
        self._inner = inner
        self._store = store
        self._floor = floor

    def recall( self, session_id: str, query_vector: list[ float ]) -> list[ Turn ]:
        similar = self._inner.recall( session_id, query_vector )
        recent = self._store.recent( session_id, self._floor )
        merged = { turn.id: turn for turn in similar }
        for turn in recent:
            merged[ turn.id ] = turn
        return sorted( merged.values(), key=lambda t: t.created_at )


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


class PeerSessionRecall:
    """Decorator over another strategy: adds turns from ONE other agent's session.

    Two agents (Toyota at the front desk, Mazda on Letta) share one store, so
    each can remember what the user told the other. The peer's turns are
    labelled in the message text -- not by inventing a new Speaker -- so a
    composer printing "AGENT: ..." never makes one agent believe it said what
    the other one did. The peer id is a callable because Toyota's session
    rotates on compact.
    """

    def __init__(
        self,
        inner: RecallStrategy,
        store: MemoryStore,
        peer_session: Callable[[], str ],
        label: str,
        limit: int = 8,
        floor: int = 4,
    ) -> None:
        self._inner = inner
        self._store = store
        self._peer_session = peer_session
        self._label = label
        self._limit = limit
        self._floor = floor

    def recall( self, session_id: str, query_vector: list[ float ]) -> list[ Turn ]:
        own = self._inner.recall( session_id, query_vector )
        peer = self._peer_session()
        if not peer or peer == session_id:
            return own
        hits = self._store.search( MemoryQuery( vector=query_vector, session_id=peer, limit=self._limit ))
        merged = { hit.turn.id: hit.turn for hit in hits }
        for turn in self._store.recent( peer, self._floor ):
            merged[ turn.id ] = turn
        labelled = [
            turn.model_copy( update={ "message": f"[{ self._label }] { turn.message }" })
            for turn in merged.values()
        ]
        return sorted( own + labelled, key=lambda t: t.created_at )
