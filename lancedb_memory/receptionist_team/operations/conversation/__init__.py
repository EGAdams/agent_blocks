"""Request-scoping Decorator; every turn uses the normal memory engine."""
from agent_memory.models import AgentReply, CompactResult

from ..interfaces import ConversationTurns, TurnContext
from ..models import ConversationRequest


class OperationalConversationAgent:
    def __init__(self, inner: ConversationTurns, context: TurnContext):
        self.inner, self.context = inner, context

    async def handle_turn(self, user_message: str, request_id: str | None = None) -> AgentReply:
        with self.context.bind(ConversationRequest(text=user_message, request_id=request_id)):
            return await self.inner.handle_turn(user_message)

    def clear_context(self) -> str:
        return self.inner.clear_context()

    async def compact(self) -> CompactResult:
        return await self.inner.compact()
