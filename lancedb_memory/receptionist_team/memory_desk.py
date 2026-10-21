"""Shared LanceDB memory for an agent whose conversation lives elsewhere.

Mazda's identity, history and tools stay in Letta; the dashboard asks this
desk for recalled context before each of her turns and hands the finished
exchange back afterwards. It reuses Toyota's warm embedder and store, so one
process owns the LanceDB table and both agents share it.
"""
from agent_memory.models import EmbeddedTurn, Turn
from agent_memory.ports import Clock, Embedder, MemoryStore, PromptComposer, RecallStrategy


class MemoryDesk:
    """Recall-then-compose before a turn, persist after it. No model calls."""

    def __init__(
        self,
        embedder: Embedder,
        store: MemoryStore,
        recall: RecallStrategy,
        composer: PromptComposer,
        clock: Clock,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._recall = recall
        self._composer = composer
        self._clock = clock

    async def compose(self, session_id: str, user_message: str) -> tuple[str, int]:
        """Return the prompt to send and how many turns were recalled."""
        vector = await self._embedder.embed(user_message)
        recalled = self._recall.recall(session_id, vector)
        return self._composer.compose(recalled, user_message), len(recalled)

    async def record(self, session_id: str, user_message: str, agent_message: str) -> None:
        turns = [
            EmbeddedTurn(
                turn=Turn(session_id=session_id, speaker=speaker, message=text,
                          created_at=self._clock.now()),
                vector=await self._embedder.embed(text),
            )
            for speaker, text in (("USER", user_message), ("AGENT", agent_message))
            if text.strip()
        ]
        if turns:
            self._store.add(turns)
