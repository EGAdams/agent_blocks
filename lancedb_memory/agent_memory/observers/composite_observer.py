from ..models import AgentReply, EmbeddedTurn, Turn
from ..ports import TurnObserver


class CompositeObserver:
    """Fans one turn out to many observers."""

    def __init__( self, observers: list[ TurnObserver ]) -> None:
        self._observers = observers

    def on_turn_complete(
        self,
        user_turn: EmbeddedTurn,
        agent_turn: EmbeddedTurn,
        recalled: list[ Turn ],
        reply: AgentReply,
    ) -> None:
        for observer in self._observers:
            observer.on_turn_complete( user_turn, agent_turn, recalled, reply )
