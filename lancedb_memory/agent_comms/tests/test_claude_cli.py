"""ClaudeCliExecutor, offline: a fake `claude` script stands in for the CLI.

    cd lancedb_memory
    .venv/bin/python -m agent_comms.tests.test_claude_cli
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_comms.executors.claude_cli import (ClaudeCliExecutor, ClaudeLoginLifetime, READ_TOOLS, WRITE_TOOLS,
                                              progress_message)
from agent_comms.models import Envelope, JailUnavailable, MessageKind

FAKE = r"""#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[ 1: ]
prompt = sys.stdin.read()
mode = os.environ.get( "FAKE_CLAUDE_MODE", "ok" )
with open( os.environ[ "FAKE_CLAUDE_LOG" ], "w" ) as log:
    json.dump({ "args": args, "prompt": prompt, "cwd": os.getcwd(), "env_key": os.environ.get( "ANTHROPIC_API_KEY" )}, log )
def emit( event ):
    print( json.dumps( event ), flush=True )
if mode == "crash":
    print( "boom: not logged in", file=sys.stderr ); sys.exit( 3 )
emit({ "type": "system", "subtype": "init", "model": "claude-sonnet-5", "apiKeySource": "none", "cwd": os.getcwd() })
if mode == "sleep":
    time.sleep( 30 )
emit({ "type": "assistant", "message": { "content": [
    { "type": "tool_use", "name": "Read", "input": { "file_path": os.path.join( os.getcwd(), "notes.md" )}}]}})
emit({ "type": "user", "message": { "content": [{ "type": "tool_result", "is_error": False, "content": "x" }]}})
emit({ "type": "assistant", "message": { "content": [
    { "type": "tool_use", "name": "Grep", "input": { "pattern": "def main" }}, { "type": "text", "text": "secret plan" }]}})
answer = "ANSWER for: " + prompt.splitlines()[ -1 ]
if mode == "leak":
    answer = "token eyJabcdefghijk.abcdefghijkl.sig"
if mode == "error":
    emit({ "type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 9, "duration_ms": 1200 })
else:
    emit({ "type": "result", "subtype": "success", "is_error": False, "result": answer, "num_turns": 3, "duration_ms": 2500 })
"""


def envelope( text: str, task_type: str = "explain", context: dict | None = None ) -> Envelope:
    return Envelope( id="t1", kind=MessageKind.REQUEST, sender="chatgpt-bridge", recipient="claude-sonnet",
                     task_type=task_type, text=text, context=context or {}, correlation_id="t1" )


class FixedLifetime:
    """ILoginLifetime test double: a login with a set number of seconds left."""

    def __init__( self, seconds: float ) -> None:
        self.seconds   = seconds
        self.refreshes = 0

    def seconds_left( self ) -> float:
        return self.seconds

    def refresh( self ) -> None:
        self.refreshes += 1


class Rig:
    def __init__( self ) -> None:
        self.dir  = Path( tempfile.mkdtemp( prefix="claude_test_" ))
        self.work = self.dir / "work"
        ( self.work / "sub" ).mkdir( parents=True )
        self.other = self.dir / "other"
        self.other.mkdir()
        self.bin  = self.dir / "claude"
        self.bin.write_text( FAKE )
        self.bin.chmod( self.bin.stat().st_mode | stat.S_IEXEC )
        self.log  = self.dir / "log.json"
        self.progress: list[ tuple[ str, str, str ]] = []
        self.login = FixedLifetime( 8 * 3600 )
        os.environ[ "FAKE_CLAUDE_LOG" ] = str( self.log )

    def executor( self, writes: bool = False, timeout: float = 20.0, **kw ) -> ClaudeCliExecutor:
        return ClaudeCliExecutor( self.work, binary=str( self.bin ), timeout_seconds=timeout, grace_seconds=1.0,
                                  writes=writes, login=self.login, jail=kw.pop( "jail", None ),
                                  progress=lambda env, phase, message: self.progress.append(( env.id, phase, message )),
                                  **kw )

    def run( self, env: Envelope, mode: str = "ok", **kw ):
        os.environ[ "FAKE_CLAUDE_MODE" ] = mode
        return asyncio.run( self.executor( **kw ).execute( env ))

    def seen( self ) -> dict:
        return json.loads( self.log.read_text())


class RecordingJail:
    def __init__( self, fail: bool = False ) -> None:
        self.calls: list[ tuple ] = []
        self.fail = fail

    def wrap( self, argv, root, cwd, writable ):
        self.calls.append(( list( argv ), Path( root ), Path( cwd ), list( writable )))
        if self.fail:
            raise JailUnavailable( "bubblewrap is not installed" )
        return argv


def test_answer_is_the_result_event_and_prompt_goes_on_stdin() -> None:
    rig     = Rig()
    hostile = "explain; rm -rf / --dangerously-skip-permissions $(id)"
    result  = rig.run( envelope( hostile ))
    assert result.ok and result.text == "ANSWER for: " + hostile, result
    assert result.performer == "claude:read-only"
    seen = rig.seen()
    assert seen[ "prompt" ].endswith( hostile ) and hostile not in seen[ "args" ]
    assert "--dangerously-skip-permissions" not in seen[ "args" ]
    assert seen[ "args" ][ seen[ "args" ].index( "--output-format" ) + 1 ] == "stream-json"
    assert Path( seen[ "cwd" ]).resolve() == rig.work.resolve()


def test_read_only_offers_only_read_tools() -> None:
    rig = Rig()
    rig.run( envelope( "hi" ))
    args = rig.seen()[ "args" ]
    assert args[ args.index( "--tools" ) + 1 ] == ",".join( READ_TOOLS )
    assert "Edit" not in READ_TOOLS and "Write" not in READ_TOOLS and "Bash" not in READ_TOOLS
    assert "Bash" in args[ args.index( "--disallowedTools" ) + 1 ]


def test_write_mode_adds_edit_and_write_but_never_bash() -> None:
    rig    = Rig()
    jail   = RecordingJail()
    result = rig.run( envelope( "add a file", "implement" ), writes=True, jail=jail )
    assert result.ok and result.performer == "claude:workspace-write"
    args = rig.seen()[ "args" ]
    assert args[ args.index( "--tools" ) + 1 ] == ",".join( WRITE_TOOLS )
    assert "Bash" not in WRITE_TOOLS and { "Edit", "Write" } <= set( WRITE_TOOLS )
    assert rig.seen()[ "prompt" ].startswith( "You are working in a dedicated workspace" )


def test_write_mode_without_a_jail_is_refused_when_built() -> None:
    try:
        ClaudeCliExecutor( Path( "/tmp" ), writes=True, jail=None, login=FixedLifetime( 9999 ))
    except ValueError as error:
        assert "jail" in str( error )
    else:
        raise AssertionError( "a write-enabled claude must need a jail" )


def test_a_writer_is_jailed_to_the_one_workspace_it_was_given() -> None:
    rig  = Rig()
    jail = RecordingJail()
    rig.run( envelope( "x", "implement", { "workdir": str( rig.work / "sub" )}), writes=True, jail=jail )
    argv, root, cwd, writable = jail.calls[ 0 ]
    assert root == ( rig.work / "sub" ).resolve() and cwd == ( rig.work / "sub" ).resolve()


def test_a_reader_is_jailed_to_the_root_holding_its_workdir() -> None:
    rig  = Rig()
    jail = RecordingJail()
    rig.run( envelope( "x", context={ "workdir": str( rig.other )}), jail=jail, read_roots=[ rig.other ])
    _, root, cwd, _ = jail.calls[ 0 ]
    assert root == rig.other.resolve() and cwd == rig.other.resolve()


def test_workdir_outside_the_roots_is_refused_without_running() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x", context={ "workdir": "/etc" }))
    assert not result.ok and "outside" in result.text
    assert not rig.log.exists()


def test_unavailable_jail_fails_and_never_runs_unjailed() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x" ), jail=RecordingJail( fail=True ))
    assert not result.ok and "jail" in result.text
    assert not rig.log.exists()


def test_progress_events_are_real_steps_without_prompt_or_reply_text() -> None:
    rig = Rig()
    rig.run( envelope( "find main" ))
    phases   = [ p for _, p, _ in rig.progress ]
    messages = " | ".join( m for _, _, m in rig.progress )
    assert phases[ 0 ] == "started" and phases[ -1 ] == "finished", rig.progress
    assert "Read notes.md" in messages and "Grep" in messages
    assert "subscription login" in messages
    assert "secret plan" not in messages and "find main" not in messages
    assert all( task == "t1" for task, _, _ in rig.progress )


def test_no_progress_is_invented_when_claude_crashes() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x" ), mode="crash" )
    assert not result.ok and "exited 3" in result.text and "not logged in" in result.text
    assert [ p for _, p, _ in rig.progress ] == [ "started" ]


def test_an_error_result_is_a_failure() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x" ), mode="error" )
    assert not result.ok and "error_max_turns" in result.text
    assert result.details[ "subtype" ] == "error_max_turns"
    assert result.details[ "error_source" ] == "claude_cli_turn_limit"
    assert result.details[ "configured_max_turns" ] == 40
    assert result.details[ "turns" ] == 9 and result.details[ "seconds" ] == 1.2
    assert result.details[ "child_pid" ] == result.details[ "process_group_id" ] > 0


def test_credential_shaped_answers_are_withheld() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x" ), mode="leak" )
    assert not result.ok and "withheld" in result.text and "eyJ" not in result.text


def test_api_key_never_reaches_claude() -> None:
    rig = Rig()
    os.environ[ "ANTHROPIC_API_KEY" ] = "sk-ant-test-not-real"
    try:
        rig.run( envelope( "x" ))
    finally:
        del os.environ[ "ANTHROPIC_API_KEY" ]
    assert rig.seen()[ "env_key" ] is None


def test_timeout_ends_the_process_group() -> None:
    rig     = Rig()
    started = time.monotonic()
    result  = rig.run( envelope( "x" ), mode="sleep", timeout=2.0 )
    assert not result.ok and "did not finish" in result.text and result.details[ "timed_out" ]
    assert time.monotonic() - started < 15


def test_a_login_near_expiry_is_refreshed_and_the_run_capped_to_it() -> None:
    rig = Rig()
    rig.login.seconds = 200
    os.environ[ "FAKE_CLAUDE_MODE" ] = "ok"
    executor = rig.executor( timeout=900 )
    assert executor.run_limit() == 200 - 60
    assert rig.login.refreshes == 1


def test_an_expired_login_fails_without_running() -> None:
    rig = Rig()
    rig.login.seconds = 30
    result = rig.run( envelope( "x" ))
    assert not result.ok and "login" in result.text
    assert not rig.log.exists()


def test_login_lifetime_reads_only_the_expiry() -> None:
    folder = Path( tempfile.mkdtemp())
    creds  = folder / ".credentials.json"
    creds.write_text( json.dumps({ "claudeAiOauth": { "accessToken": "a" * 40, "expiresAt": ( time.time() + 3600 ) * 1000 }}))
    left = ClaudeLoginLifetime( creds, refresh_command=[ "true" ]).seconds_left()
    assert 3500 < left <= 3600
    assert ClaudeLoginLifetime( folder / "missing.json", refresh_command=[ "true" ]).seconds_left() == 0


def test_progress_message_shapes() -> None:
    root = Path( "/w" )
    assert progress_message({ "type": "assistant", "message": { "content": [
        { "type": "tool_use", "name": "Edit", "input": { "file_path": "/w/a/b.py" }}]}}, root ) == [( "tool", "Edit a/b.py" )]
    assert progress_message({ "type": "assistant", "message": { "content": [{ "type": "text", "text": "hi" }]}}, root ) == []
    assert progress_message({ "type": "user", "message": { "content": [{ "type": "tool_result", "is_error": True }]}}, root ) \
        == [( "tool_error", "a tool call failed; Claude continues" )]


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]

if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS ) } passed" )
