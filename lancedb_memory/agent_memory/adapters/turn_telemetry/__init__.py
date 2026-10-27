"""Observer decorators for existing ports; the conversation engine stays unchanged."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4
from collections.abc import Iterator

from ...models import AgentReply, EmbeddedTurn, MemoryQuery, RecalledTurn, Turn
from ...ports import Embedder, LanguageModel, MemoryStore, RecallStrategy

from .models import StageTiming

_request_id: ContextVar[ str ] = ContextVar( "turn_request_id", default="local" )


@contextmanager
def turn_scope( request_id: str | None ) -> Iterator[ None ]:
    token = _request_id.set( request_id or uuid4().hex )
    try:
        yield
    finally:
        _request_id.reset( token )


def record_timing( stage: str, duration_s: float, outcome: str = "ok" ) -> None:
    record = StageTiming( request_id=_request_id.get(), stage=stage,
                         duration_ms=round( duration_s * 1000, 3 ), outcome=outcome )
    try:
        print( "[turn-timing] " + record.model_dump_json(), flush=True )
    except OSError:
        pass  # Logging must never break a turn or expose conversation content.


@contextmanager
def timed_stage( stage: str ) -> Iterator[ None ]:
    started, outcome = perf_counter(), "ok"
    try:
        yield
    except BaseException as error:
        outcome = type( error ).__name__
        raise
    finally:
        record_timing( stage, perf_counter() - started, outcome )


class TimedModel:
    def __init__( self, inner: LanguageModel, stage: str ) -> None:
        self._inner, self._stage = inner, stage

    async def complete( self, prompt: str ) -> AgentReply:
        with timed_stage( self._stage ):
            return await self._inner.complete( prompt )


class TimedEmbedder:
    def __init__( self, inner: Embedder ) -> None:
        self._inner = inner

    @property
    def dimensions( self ) -> int:
        return self._inner.dimensions

    async def embed( self, text: str ) -> list[ float ]:
        with timed_stage( "embedding" ):
            return await self._inner.embed( text )


class TimedRecall:
    def __init__( self, inner: RecallStrategy ) -> None:
        self._inner = inner

    def recall( self, session_id: str, vector: list[ float ] ) -> list[ Turn ]:
        with timed_stage( "recall" ):
            return self._inner.recall( session_id, vector )


class TimedStore:
    def __init__( self, inner: MemoryStore ) -> None:
        self._inner = inner

    def add( self, turns: list[ EmbeddedTurn ] ) -> None:
        with timed_stage( "persistence" ):
            self._inner.add( turns )

    def search( self, query: MemoryQuery ) -> list[ RecalledTurn ]:
        with timed_stage( "memory_search" ):
            return self._inner.search( query )

    def recent( self, session_id: str, limit: int ) -> list[ Turn ]:
        with timed_stage( "memory_recent" ):
            return self._inner.recent( session_id, limit )

    def forget( self, ids: list[ str ] ) -> int:
        return self._inner.forget( ids )
