"""Decorators over the ports. Same interface in, same interface out."""
import asyncio

from ..models import AgentReply
from ..ports import Embedder, LanguageModel


class RetryingModel:
    """Pulls the retry loop out of the completion call, where it was welded in."""

    def __init__( self, inner: LanguageModel, max_retry: int = 5, backoff: float = 1.0 ) -> None:
        self._inner = inner
        self._max_retry = max_retry
        self._backoff = backoff

    async def complete( self, prompt: str ) -> AgentReply:
        last: Exception | None = None
        for attempt in range( self._max_retry ):
            try:
                return await self._inner.complete( prompt )
            except Exception as oops:
                last = oops
                print( f"\nError communicating with the model ( attempt { attempt + 1 }): { oops }" )
                await asyncio.sleep( self._backoff * ( attempt + 1 ))
        raise RuntimeError( f"Model failed after { self._max_retry } attempts" ) from last


class CachingEmbedder:
    """Identical text embeds once per process. Cheap win on repeated turns."""

    def __init__( self, inner: Embedder ) -> None:
        self._inner = inner
        self._cache: dict[ str, list[ float ]] = {}

    @property
    def dimensions( self ) -> int:
        return self._inner.dimensions

    async def embed( self, text: str ) -> list[ float ]:
        if text not in self._cache:
            self._cache[ text ] = await self._inner.embed( text )
        return self._cache[ text ]
