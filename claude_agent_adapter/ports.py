"""The Protocol. adapter.py depends on this and on .models, and nothing else.

Mirrors IConversationAgent
(voice_communication/conversation_agent/conversation_agent.ts) for the
claude-agent-sdk side of the boundary -- see the self-containment note in
models.py for why this is a local copy rather than a cross-package import.
"""
from collections.abc import AsyncIterator
from typing import Protocol

from .models import AgentEvent


class ConversationAgent( Protocol ):
    def submit( self, text: str, generation: int ) -> AsyncIterator[ AgentEvent ]: ...
