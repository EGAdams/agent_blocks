from ..models import AgentReply, EmbeddedTurn, Turn


class CostObserver:
    """Accumulates usage across the session."""

    def __init__( self ) -> None:
        self.total_cost_usd: float = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0

    def on_turn_complete(
        self,
        user_turn: EmbeddedTurn,
        agent_turn: EmbeddedTurn,
        recalled: list[ Turn ],
        reply: AgentReply,
    ) -> None:
        self.total_cost_usd += reply.cost_usd or 0.0
        self.total_input_tokens += reply.input_tokens or 0
        self.total_output_tokens += reply.output_tokens or 0
