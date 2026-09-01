"""Fakes for testing ClaudeAgentAdapter with no network and no API key.

Builds real claude_agent_sdk message objects (AssistantMessage, TextBlock,
ResultMessage) so adapter.py's isinstance checks pass without any SDK call.
"""
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock


def scripted_query( messages, captured_prompts=None ):
    """Returns a fake replacing claude_agent_sdk.query; ignores `options`."""

    async def fake_query( *, prompt, options=None ):
        if captured_prompts is not None:
            captured_prompts.append( prompt )
        for message in messages:
            yield message

    return fake_query


def assistant_text( text, model="claude-haiku-4-5-20251001" ) -> AssistantMessage:
    return AssistantMessage( content=[ TextBlock( text=text )], model=model )


def result( *, is_error=False, text=None, session_id="fake-session" ) -> ResultMessage:
    return ResultMessage(
        subtype="success" if not is_error else "error_during_execution",
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        result=text,
    )
