# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""ClaudeAgentAdapter -- the ConversationAgent port backed by claude-agent-sdk.

Same auth requirement as catherine_agent_sdk/basic_agent.py and
lancedb_memory/agent_memory/adapters/claude_model.py: ANTHROPIC_API_KEY and
ANTHROPIC_BASE_URL must stay UNSET so the SDK uses the local Claude Code
subscription auth instead of direct-API billing.

ResultMessage.is_error is where auth/model/execution failures surface; they
do not raise. submit() checks it before yielding a terminal event, so a
failed call never reaches the caller looking like a success.
"""
from collections.abc import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from .models import AgentEvent, AgentEventKind


class AgentCallFailed( RuntimeError ):
    pass


class ClaudeAgentAdapter:
    """Opens a fresh claude-agent-sdk session for every submit() call.

    Reproducible, not addressable: nothing here ties an instance back to any
    particular running Claude Code conversation -- including the one that
    wrote this file. Any other instance can construct the same adapter from
    this same recipe and get the same behavior.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        max_turns: int = 1,
        system_prompt: str | None = None,
    ) -> None:
        self._model = model
        self._max_turns = max_turns
        self._system_prompt = system_prompt

    def _options( self ) -> ClaudeAgentOptions:
        # setting_sources=[] keeps the SDK from loading the enclosing
        # project's CLAUDE.md and settings; allowed_tools=[] keeps it from
        # reading files. This adapter answers with text only.
        return ClaudeAgentOptions(
            model=self._model,
            max_turns=self._max_turns,
            system_prompt=self._system_prompt,
            setting_sources=[],
            allowed_tools=[],
        )

    async def submit(
        self,
        text: str,
        generation: int,
    ) -> AsyncIterator[ AgentEvent ]:
        final_result: ResultMessage | None = None

        async for message in query( prompt=text, options=self._options()):
            if isinstance( message, AssistantMessage ):
                for block in message.content:
                    if isinstance( block, TextBlock ):
                        yield AgentEvent(
                            generation=generation,
                            kind=AgentEventKind.ASSISTANT_TEXT,
                            text=block.text,
                        )
            elif isinstance( message, ResultMessage ):
                final_result = message

        if final_result is None:
            raise AgentCallFailed( "No ResultMessage was returned by the Agent SDK." )

        if final_result.is_error:
            raise AgentCallFailed( f"Agent SDK error: { final_result.result }" )

        yield AgentEvent(
            generation=generation,
            kind=AgentEventKind.TERMINAL,
            text=final_result.result or "",
        )
