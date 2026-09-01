"""ClaudeAgentAdapter tests. No network, no API key.

Run:  ../lancedb_memory/.venv/bin/python tests/test_adapter.py
(claude-agent-sdk is not installed in the default interpreter; the
lancedb_memory venv already has it.)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent.parent.parent ))

import claude_agent_adapter.adapter as adapter_module
from claude_agent_adapter.adapter import AgentCallFailed, ClaudeAgentAdapter
from claude_agent_adapter.models import AgentEventKind
from claude_agent_adapter.tests.fakes import assistant_text, result, scripted_query


async def test_submit_streams_assistant_text_then_terminal():
    prompts = []
    adapter_module.query = scripted_query(
        [ assistant_text( "Hello there." ), result( text="Hello there." )],
        captured_prompts=prompts,
    )
    adapter = ClaudeAgentAdapter()

    events = [ event async for event in adapter.submit( "hi", generation=1 )]

    assert prompts == [ "hi" ], prompts
    assert [ e.kind for e in events ] == [
        AgentEventKind.ASSISTANT_TEXT,
        AgentEventKind.TERMINAL,
    ], events
    assert events[ 0 ].text == "Hello there.", events[ 0 ]
    assert all( e.generation == 1 for e in events ), events


async def test_submit_raises_on_result_error():
    adapter_module.query = scripted_query(
        [ result( is_error=True, text="authentication_failed" )],
    )
    adapter = ClaudeAgentAdapter()

    try:
        _ = [ event async for event in adapter.submit( "hi", generation=0 )]
        raise AssertionError( "expected AgentCallFailed" )
    except AgentCallFailed as exc:
        assert "authentication_failed" in str( exc ), exc


async def test_submit_raises_when_no_result_message_arrives():
    adapter_module.query = scripted_query([ assistant_text( "orphaned text" )])
    adapter = ClaudeAgentAdapter()

    try:
        _ = [ event async for event in adapter.submit( "hi", generation=0 )]
        raise AssertionError( "expected AgentCallFailed" )
    except AgentCallFailed as exc:
        assert "No ResultMessage" in str( exc ), exc


async def test_multiple_text_blocks_each_become_their_own_event():
    adapter_module.query = scripted_query(
        [ assistant_text( "part one " ), assistant_text( "part two" ), result( text="part one part two" )],
    )
    adapter = ClaudeAgentAdapter()

    events = [ event async for event in adapter.submit( "hi", generation=7 )]

    texts = [ e.text for e in events if e.kind == AgentEventKind.ASSISTANT_TEXT ]
    assert texts == [ "part one ", "part two" ], texts


async def main():
    tests = [ value for name, value in sorted( globals().items()) if name.startswith( "test_" )]
    failures = 0
    for test in tests:
        try:
            await test()
            print( f"PASS  { test.__name__ }" )
        except AssertionError as oops:
            failures += 1
            print( f"FAIL  { test.__name__ }: { oops }" )
    print( f"\n{ len( tests ) - failures }/{ len( tests )} passed" )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit( asyncio.run( main()))
