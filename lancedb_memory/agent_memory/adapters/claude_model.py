"""LanguageModel over the Claude Agent SDK.

Uses the local Claude Code subscription auth, so ANTHROPIC_API_KEY and
ANTHROPIC_BASE_URL must be UNSET -- same requirement as catherine_agent_sdk.

ResultMessage.is_error is where auth/model/execution failures surface; they do
not raise. This adapter converts that flag into an exception so the agent loop
never treats an error string as a reply.
"""
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from ..models import AgentReply


DEFAULT_SYSTEM_PROMPT = (
    "You are a conversational assistant with long-term memory. Answer the user "
    "directly and conversationally. Your only context is the recalled "
    "conversation you are given."
)


class ClaudeModelError( RuntimeError ):
    pass


class ClaudeAgentModel:
    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        stream: bool = True,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self._model = model
        self._stream = stream
        self._system_prompt = system_prompt

    async def complete( self, prompt: str ) -> AgentReply:
        # setting_sources=[] keeps the SDK from loading the enclosing project's
        # CLAUDE.md and settings; allowed_tools=[] keeps it from reading files.
        # Conversational memory comes from OUR store, not from the repo it runs in.
        options = ClaudeAgentOptions(
            model=self._model,
            max_turns=1,
            system_prompt=self._system_prompt,
            setting_sources=[],
            allowed_tools=[],
        )

        chunks: list[ str ] = []
        final_result: ResultMessage | None = None

        async for message in query( prompt=prompt, options=options ):
            if isinstance( message, AssistantMessage ):
                for block in message.content:
                    if isinstance( block, TextBlock ):
                        chunks.append( block.text )
                        if self._stream:
                            print( block.text, end="", flush=True )
            elif isinstance( message, ResultMessage ):
                final_result = message

        if final_result is None:
            raise ClaudeModelError( "No ResultMessage was returned by the Agent SDK." )

        if final_result.is_error:
            raise ClaudeModelError( f"Agent SDK error: { final_result.result }" )

        text = "".join( chunks ).strip() or ( final_result.result or "" ).strip()
        usage = final_result.usage or {}

        return AgentReply(
            text=text,
            input_tokens=usage.get( "input_tokens" ),
            output_tokens=usage.get( "output_tokens" ),
            cost_usd=final_result.total_cost_usd,
        )
