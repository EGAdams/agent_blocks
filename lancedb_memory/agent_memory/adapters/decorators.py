"""Decorators over the ports. Same interface in, same interface out."""
import asyncio

from ..errors import ModelUnavailable
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
            except ModelUnavailable:
                # Nothing to wait out. Five tries over fifteen seconds cannot
                # clear a daily token limit, and the provider's own message
                # already says when it will -- so let it reach the user.
                raise
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


class FallbackModel:
    """Tries each model in turn, so one provider's wall is not the end of the turn.

    Same shape as RetryingModel: a LanguageModel in, a LanguageModel out. The
    agent sees one complete() call and never learns which backend answered.

    RetryingModel handles "wait a second and ask again". This handles the other
    kind of failure -- the kind waiting cannot fix, like Groq's 200000 tokens
    per model per day running out at turn sixty. Wrap each backend in its own
    RetryingModel and this around the lot:

        FallbackModel( RetryingModel( groq ), [ RetryingModel( openai )])

    Which backends exist is the composition root's decision, not this class's:
    main.py is the only module allowed to name a concrete model, and it is also
    the only place that knows whether OPENAI_API_KEY is set. Pass an empty
    `backups` and this behaves exactly like the bare inner model, which is what
    a Groq-only machine gets.

    It falls back on ModelUnavailable (the adapters' "waiting will not help"
    signal) and on RuntimeError, which covers RetryingModel giving up and both
    provider error classes, since each subclasses it. It deliberately does NOT
    catch TypeError and friends: a bug in our own prompt-building should surface
    as a bug, not quietly cost a second paid API call on every turn.
    """

    def __init__(
        self,
        inner: LanguageModel,
        backups: list[ LanguageModel ],
        names: list[ str ] | None = None,
    ) -> None:
        self._chain = [ inner, *backups ]
        # Only for the console line below. A backend that cannot be named is
        # still usable, so this never has to line up with the chain.
        self._names = names or []

    def _name( self, index: int ) -> str:
        if index < len( self._names ):
            return self._names[ index ]
        return type( self._chain[ index ]).__name__

    async def complete( self, prompt: str ) -> AgentReply:
        last: Exception | None = None
        for index, model in enumerate( self._chain ):
            try:
                return await model.complete( prompt )
            except ( ModelUnavailable, RuntimeError ) as oops:
                last = oops
                remaining = len( self._chain ) - index - 1
                if not remaining:
                    break
                # Said out loud every time. A receptionist silently costing
                # money because the free backend has been dead for a week is
                # the failure mode this line exists to prevent.
                # flush=True is load-bearing, not tidiness. A sidecar's
                # stdout is a redirected file, so print() is block-buffered
                # and this warning would sit unseen in an 8KB buffer for
                # hours -- which is exactly the silent paid usage the line
                # exists to prevent. Found the hard way reading an empty log.
                print(
                    f"\n[{ self._name( index )} unavailable: { oops }]"
                    f"\n[falling back to { self._name( index + 1 )}]",
                    flush=True,
                )
        assert last is not None
        raise last
