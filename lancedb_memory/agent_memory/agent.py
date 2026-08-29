"""High-level conversation policy.

This module may import .models and .ports and NOTHING else. No lancedb, no
claude_agent_sdk, no filesystem. That rule is the DIP boundary; if an import
line ever grows here, the design has regressed.
"""
from .models import AgentReply, EmbeddedTurn, Turn
from .ports import (
    Clock,
    Embedder,
    LanguageModel,
    MemoryStore,
    PromptComposer,
    RecallStrategy,
    TurnObserver,
)


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
    ) -> None:
        self._session_id = session_id
        self._embedder = embedder
        self._store = store
        self._recall = recall
        self._composer = composer
        self._model = model
        self._clock = clock
        self._observer = observer

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
