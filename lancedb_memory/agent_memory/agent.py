"""High-level conversation policy.

This module may import .models and .ports and NOTHING else. No lancedb, no
claude_agent_sdk, no filesystem. That rule is the DIP boundary; if an import
line ever grows here, the design has regressed.
"""
from .models import AgentReply, CompactResult, EmbeddedTurn, Turn
from .ports import (
    Clock,
    Embedder,
    LanguageModel,
    MemoryStore,
    PromptComposer,
    RecallStrategy,
    TurnObserver,
)

# What the summarizer is asked; <<CONVERSATION>> is the session's recent turns.
COMPACT_PROMPT = """Below is our conversation so far. Write a compact summary of it that you
can rely on in place of the full transcript: who the user is, what they asked
for, what was decided or done, any names, numbers or dates that matter, and
anything still open. Plain prose or short bullets, under 250 words. Reply with
the summary only.

<<CONVERSATION>>"""

SUMMARY_LABEL = "[Summary of our earlier conversation]"


class ConversationAgent:
    """Facade over the whole turn cycle: embed, recall, compose, complete, persist."""

    def __init__(
        self,
        session_id: str,
        embedder: Embedder,
        store: MemoryStore,
        recall: RecallStrategy,
        composer: PromptComposer,
        model: LanguageModel,
        clock: Clock,
        observer: TurnObserver,
        summarizer: LanguageModel | None = None,
        compact_window: int = 60,
    ) -> None:
        self._session_id = session_id
        self._session_root = session_id
        self._context_epoch = 0
        self._embedder = embedder
        self._store = store
        self._recall = recall
        self._composer = composer
        self._model = model
        self._clock = clock
        self._observer = observer
        # Compaction asks a plain model, not the turn model: the turn model may
        # route to roles or stream deltas into whatever turn last bound them.
        self._summarizer = summarizer or model
        self._compact_window = compact_window

    async def handle_turn( self, user_message: str ) -> AgentReply:
        user_vector = await self._embedder.embed( user_message )

        # Recall BEFORE persisting, so the current turn is never its own context.
        recalled = self._recall.recall( self._session_id, user_vector )

        prompt = self._composer.compose( recalled, user_message )
        reply = await self._model.complete( prompt )

        user_turn = EmbeddedTurn(
            turn=Turn(
                session_id=self._session_id,
                speaker="USER",
                message=user_message,
                created_at=self._clock.now(),
            ),
            vector=user_vector,
        )
        agent_turn = EmbeddedTurn(
            turn=Turn(
                session_id=self._session_id,
                speaker="AGENT",
                message=reply.text,
                created_at=self._clock.now(),
            ),
            vector=await self._embedder.embed( reply.text ),
        )

        self._store.add([ user_turn, agent_turn ])
        self._observer.on_turn_complete( user_turn, agent_turn, recalled, reply )

        return reply

    def clear_context( self ) -> str:
        """Start a fresh recall scope -- a "compact conversation" button.

        Nothing is deleted: every turn already persisted stays in the store
        under its original session id, and RecallStrategy is session-scoped
        (see recall.py / test_sessions_are_isolated), so rotating onto a new
        id is enough to make the next turn's recall find nothing from before
        -- without a destructive delete anywhere on this path.
        """
        self._context_epoch += 1
        self._session_id = f"{ self._session_root }--ctx{ self._context_epoch }"
        return self._session_id

    async def compact( self ) -> CompactResult:
        """Summarize this session, then carry on in a new one seeded with it.

        The model writes the summary BEFORE the session rotates, so a failed
        call raises with nothing changed -- the conversation keeps its context
        rather than losing it to a half-done compact. The old turns stay in
        the store under their own session id, as with clear_context().
        """
        turns = list( reversed( self._store.recent( self._session_id, self._compact_window )))
        if not turns:
            return CompactResult( session_id=self.clear_context(), summary="", turns_before=0 )
        conversation = "\n".join( f"{ turn.speaker }: { turn.message }" for turn in turns )
        reply = await self._summarizer.complete( COMPACT_PROMPT.replace( "<<CONVERSATION>>", conversation ))
        summary = reply.text.strip()
        if not summary:
            raise ValueError( "the model returned an empty summary" )
        session_id = self.clear_context()
        message = f"{ SUMMARY_LABEL } { summary }"
        self._store.add([ EmbeddedTurn(
            turn=Turn( session_id=session_id, speaker="AGENT", message=message, created_at=self._clock.now()),
            vector=await self._embedder.embed( message ),
        )])
        return CompactResult( session_id=session_id, summary=summary, turns_before=len( turns ))
