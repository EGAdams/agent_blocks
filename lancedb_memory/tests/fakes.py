"""Test doubles. Nothing here touches the network or the disk."""
from datetime import datetime, timedelta, timezone

from agent_memory.models import AgentReply, EmbeddedTurn, Turn


class ScriptedModel:
    """LanguageModel that returns queued replies and records the prompts it saw."""

    def __init__( self, replies: list[ str ]) -> None:
        self._replies = list( replies )
        self.prompts: list[ str ] = []

    async def complete( self, prompt: str ) -> AgentReply:
        self.prompts.append( prompt )
        text = self._replies.pop( 0 ) if self._replies else "(no scripted reply)"
        return AgentReply( text=text, input_tokens=10, output_tokens=5, cost_usd=0.001 )


class TickingClock:
    """Advances one second per call so turn ordering is deterministic."""

    def __init__( self, start: datetime | None = None ) -> None:
        self._current = start or datetime( 2026, 1, 1, tzinfo=timezone.utc )

    def now( self ) -> datetime:
        value = self._current
        self._current = self._current + timedelta( seconds=1 )
        return value


class RecordingObserver:
    def __init__( self ) -> None:
        self.calls: list[ tuple[ EmbeddedTurn, EmbeddedTurn, list[ Turn ], AgentReply ]] = []

    def on_turn_complete(
        self,
        user_turn: EmbeddedTurn,
        agent_turn: EmbeddedTurn,
        recalled: list[ Turn ],
        reply: AgentReply,
    ) -> None:
        self.calls.append( ( user_turn, agent_turn, recalled, reply ))
