"""Live output and honest outcomes. Offline: a fake codex that is a real process.

    cd lancedb_memory && .venv/bin/python -m agent_comms.tests.test_live_output
"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from agent_bridge_worker.models import AgentOutcome, OutcomeKind
from agent_bridge_worker.service import judged_write
from agent_comms.executors.claude_cli import output_message
from agent_comms.executors.codex_cli import CodexCliExecutor, codex_steps
from agent_comms.live_output import clean, event_name
from agent_comms.models import Envelope, MessageKind, TaskResult
from agent_comms.outcome import JudgedExecutor, judge

BLOCKED_REPLY = ( "Blocked by pre-existing overlapping worktree changes.\n\nNo files were created or changed." )


def _envelope( task_type: str = "implement" ) -> Envelope:
    return Envelope( id="x", kind=MessageKind.REQUEST, sender="a", recipient="b", task_type=task_type,
                     text="go", correlation_id="x" )


def _fake_codex( folder: Path, body: str ) -> str:
    path = folder / "codex"
    path.write_text( f"#!{ sys.executable }\nimport json, sys, time\nsys.stdin.read()\n"
                     "out = sys.argv[ sys.argv.index( '-o' ) + 1 ]\n"
                     "def say( **e ): print( json.dumps( e ), flush=True )\n" + body )
    path.chmod( 0o755 )
    return str( path )


def test_codex_steps_arrive_before_it_finishes_and_reasoning_never() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        binary = _fake_codex( Path( tmp ), (
            "say( type='item.completed', item={ 'type': 'reasoning', 'text': 'PRIVATE' } )\n"
            "say( type='item.started', item={ 'type': 'command_execution', 'command': 'pytest -q' } )\n"
            "time.sleep( 1 )\n"
            "say( type='item.completed', item={ 'type': 'command_execution', 'aggregated_output': '3 passed', 'exit_code': 0 } )\n"
            "print( 'a warning', file=sys.stderr, flush=True )\n"
            "open( out, 'w' ).write( 'done' )\n" ))
        seen: list[ tuple[ float, str, str ]] = []
        executor = CodexCliExecutor( Path( tmp ), binary=binary,
                                     progress=lambda e, phase, message: seen.append(( time.monotonic(), phase, message )))
        result = asyncio.run( executor.execute( _envelope( "inspect" )))
        ended = time.monotonic()
    assert result.ok, result.text
    first = next( at for at, phase, _ in seen if phase == "command" )
    assert ended - first > 0.8, "the command was only reported after the run"
    text = " | ".join( m for _, _, m in seen )
    assert "$ pytest -q" in text and "3 passed" in text and "a warning" in text and "PRIVATE" not in text


def test_a_failed_turn_that_exits_zero_is_a_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        binary = _fake_codex( Path( tmp ), "say( type='turn.failed', error={ 'message': 'token could not be refreshed' } )\n"
                                           "open( out, 'w' ).write( 'x' )\n" )
        result = asyncio.run( CodexCliExecutor( Path( tmp ), binary=binary ).execute( _envelope( "inspect" )))
    assert not result.ok and "token could not be refreshed" in result.text


class _Says:
    performer = "fake"

    def __init__( self, text: str ) -> None:
        self.text = text

    async def execute( self, envelope: Envelope ) -> TaskResult:
        return TaskResult( ok=True, text=self.text, performer="fake" )


def test_the_real_blocked_reply_is_not_a_success() -> None:
    result = asyncio.run( JudgedExecutor( _Says( BLOCKED_REPLY )).execute( _envelope()))
    assert not result.ok and result.details[ "verdict" ] == "blocked" and result.details[ "retryable" ] is False
    assert asyncio.run( JudgedExecutor( _Says( BLOCKED_REPLY )).execute( _envelope( "explain" ))).ok


def test_outcome_lines() -> None:
    assert judge( "x\nOUTCOME: BLOCKED - dirty files" ) == ( "blocked", "dirty files" )
    assert judge( "x\n**OUTCOME: NO_CHANGES — already so**" )[ 0 ] == "completed_without_changes"
    assert judge( "Blocked users are now listed.\nOUTCOME: IMPLEMENTED" )[ 0 ] == "implemented"
    assert judge( "I added the pane." )[ 0 ] == "implemented"


def test_a_write_with_no_changes_or_failed_checks_is_not_a_success() -> None:
    good = AgentOutcome( kind=OutcomeKind.SUCCEEDED, summary="done" )
    assert judged_write( good, { "outcome": "no_changes" }).kind is OutcomeKind.FAILED
    assert judged_write( good, { "outcome": "changed", "checks_run": [{ "passed": False }]}).kind is OutcomeKind.FAILED
    assert judged_write( good, { "outcome": "changed", "checks_run": [{ "passed": True }]}) is good


def test_detail_stays_local_and_secrets_are_withheld() -> None:
    assert event_name( "output" ) == "task.output" and event_name( "tool" ) == "task.progress"
    assert "hunter22" not in clean( "DB_PASSWORD=hunter22222" )
    event = { "type": "assistant", "message": { "content": [
        { "type": "thinking", "thinking": "PRIVATE" }, { "type": "tool_use", "name": "Bash", "input": { "command": "ls" }}]}}
    assert output_message( event ) == [( "command", "$ ls" )]
    assert codex_steps({ "type": "item.completed", "item": { "type": "reasoning", "text": "PRIVATE" }}) == []


if __name__ == "__main__":
    tests = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
    for test in tests:
        test()
        print( f"ok  { test.__name__ }" )
    print( f"{ len( tests )} passed" )
