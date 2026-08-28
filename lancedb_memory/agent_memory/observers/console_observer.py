from ..models import AgentReply, EmbeddedTurn, Turn


class ConsoleObserver:
    """Replaces the ad-hoc 'file found:' prints with one recall summary line."""

    def on_turn_complete(
        self,
        user_turn: EmbeddedTurn,
        agent_turn: EmbeddedTurn,
        recalled: list[ Turn ],
        reply: AgentReply,
    ) -> None:
        print( f"\n[memory] recalled { len( recalled )} turn(s) as context" )
