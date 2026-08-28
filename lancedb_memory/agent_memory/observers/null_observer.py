from ..models import AgentReply, EmbeddedTurn, Turn


class NullObserver:
    """Null Object. Lets the agent run with no side effects and no None-checks."""

    def on_turn_complete(
        self,
        user_turn: EmbeddedTurn,
        agent_turn: EmbeddedTurn,
        recalled: list[ Turn ],
        reply: AgentReply,
    ) -> None:
        return None
