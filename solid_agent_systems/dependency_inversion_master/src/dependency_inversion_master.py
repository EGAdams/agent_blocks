# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)


async def run_agent():
    options = ClaudeAgentOptions(
        model="claude-haiku-4-5-20251001",
        max_turns=1,
    )

    print( "Starting agent task...\n" )

    final_result = None

    async for message in query(
        prompt="Are you able to access the web?",
        options=options,
    ):
        if isinstance( message, AssistantMessage ):
            for block in message.content:
                if isinstance( block, TextBlock ):
                    print( block.text, end="", flush=True )

        elif isinstance( message, ResultMessage ):
            final_result = message

    print( "\n\nTask completed!" )

    if final_result is None:
        print( "No ResultMessage was returned." )
        return

    print( f"Status: { final_result.subtype }" )
    print( f"Is error: { final_result.is_error }" )
    print( f"Session ID: { final_result.session_id }" )

    # This exposes authentication, model, and other execution errors.
    if final_result.is_error:
        print( f"Error: { final_result.result }" )
    elif final_result.result:
        # Usually duplicates the AssistantMessage text, so this is mainly
        # useful as a fallback or for debugging.
        print( f"Final result: { final_result.result }" )

    usage = final_result.usage or {}

    print( f"Input Tokens: { usage.get( 'input_tokens', 0 )}" )
    print( f"Output Tokens: { usage.get( 'output_tokens', 0 )}" )
    print(
        "Cached Input Tokens Read: "
        f"{ usage.get( 'cache_read_input_tokens', 0 )}"
    )
    print(
        "Cache Creation Tokens: "
        f"{ usage.get( 'cache_creation_input_tokens', 0 )}"
    )

    if final_result.total_cost_usd is not None:
        print( f"Total Cost: ${ final_result.total_cost_usd:.6f }" )

    for model, stats in ( final_result.model_usage or {}).items():
        print( f"Model: { model  }" )
        print( f"Context Window: { stats['contextWindow' ]:, } tokens" )
        print( f"Maximum Output: { stats['maxOutputTokens' ]:, } tokens" )


if __name__ == "__main__":
    asyncio.run( run_agent())

# https://chatgpt.com/c/6a81b0b8-e880-83ea-bf1e-d0879621d95e

