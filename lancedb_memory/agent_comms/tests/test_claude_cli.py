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

from agent_comms.executors.claude_cli import (ClaudeCliExecutor, ClaudeLoginLifetime, READ_TOOLS, SHELL_TOOLS, WRITE_TOOLS,
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
emit({ "type": "system", "subtype": "init", "model": os.environ.get( "FAKE_CLAUDE_INIT_MODEL", "claude-sonnet-5" ), "apiKeySource": "none", "cwd": os.getcwd() })
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
    served = os.environ.get( "FAKE_CLAUDE_SERVED" )
    usage  = { m: { "outputTokens": 5 } for m in served.split( "," )} if served else {}
    emit({ "type": "result", "subtype": "success", "is_error": False, "result": answer, "num_turns": 3, "duration_ms": 2500,
           "modelUsage": usage })
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

    def write_jail_copy( self, destination: Path ) -> None:
        self.copies = getattr( self, "copies", [] ) + [ Path( destination )]


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


def test_a_writing_capability_gets_the_cards_write_budget_and_a_reader_keeps_its_own() -> None:
    from agent_comms.factory import default_builders
    from agent_comms.models import AgentCard, Capability
    folder = Path( tempfile.mkdtemp())
    login  = folder / "login.json"
    login.write_text( "{}" )
    card = AgentCard( name="claude-sonnet", display_name="Sonnet", runtime="claude_cli",
                      settings={ "claude_home": str( folder / "home" ), "login_file": str( login ),
                                 "workdir": str( folder ), "write_workdir": str( folder ),
                                 "timeout_seconds": 900, "max_turns": 40,
                                 "write_timeout_seconds": 1800, "write_max_turns": 100 },
                      capabilities=[ Capability( task_type="plan", description="look" ),
                                     Capability( task_type="implement", description="build", writes=True )])
    chosen = default_builders( None, None, folder )[ "claude_cli" ]( card )._executors
    assert ( chosen[ "implement" ]._turns, chosen[ "implement" ]._timeout ) == ( 100, 1800.0 )
    assert ( chosen[ "plan" ]._turns, chosen[ "plan" ]._timeout ) == ( 40, 900.0 )
    del card.settings[ "write_max_turns" ], card.settings[ "write_timeout_seconds" ]
    chosen = default_builders( None, None, folder )[ "claude_cli" ]( card )._executors
    assert ( chosen[ "implement" ]._turns, chosen[ "implement" ]._timeout ) == ( 40, 900.0 )


def test_a_shell_is_offered_to_a_writer_that_asks_for_one_and_never_to_a_reader() -> None:
    rig    = Rig()
    result = rig.run( envelope( "run the tests", "implement" ), writes=True, jail=RecordingJail(), shell=True )
    assert result.ok and result.performer == "claude:workspace-write+shell"
    args = rig.seen()[ "args" ]
    assert args[ args.index( "--tools" ) + 1 ] == ",".join( SHELL_TOOLS ) and "Bash" in SHELL_TOOLS
    assert "Bash" not in args[ args.index( "--disallowedTools" ) + 1 ].split( "," )
    assert "WebFetch" in args[ args.index( "--disallowedTools" ) + 1 ]
    assert rig.seen()[ "prompt" ].startswith( "You are working in a dedicated workspace" ) and "Bash tool" in rig.seen()[ "prompt" ]
    try:
        rig.executor( writes=False, shell=True )
    except ValueError as error:
        assert "write-enabled" in str( error )
    else:
        raise AssertionError( "a reader must never be given a shell" )


def test_the_jail_sees_the_login_without_its_refresh_token_and_the_original_is_untouched() -> None:
    folder = Path( tempfile.mkdtemp())
    creds  = folder / ".credentials.json"
    full   = { "claudeAiOauth": { "accessToken": "a" * 40, "refreshToken": "r" * 40, "refreshTokenExpiresAt": 5,
                                  "expiresAt": ( time.time() + 3600 ) * 1000, "subscriptionType": "max" }}
    creds.write_text( json.dumps( full ))
    target = folder / "jail" / "x.credentials.json"
    ClaudeLoginLifetime( creds, refresh_command=[ "true" ]).write_jail_copy( target )
    copy = json.loads( target.read_text())[ "claudeAiOauth" ]
    assert copy[ "accessToken" ] == "a" * 40 and copy[ "subscriptionType" ] == "max"
    assert "refreshToken" not in copy and "refreshTokenExpiresAt" not in copy
    assert ( target.stat().st_mode & 0o777 ) == 0o600 and not list( target.parent.glob( "*.tmp" ))
    assert json.loads( creds.read_text()) == full, "the real login must not change"


def test_the_copy_for_the_jail_is_made_after_the_login_is_checked_and_before_the_jail_is_built() -> None:
    rig  = Rig()
    jail = RecordingJail()
    target = rig.dir / "jail_login.json"
    rig.run( envelope( "x", "implement" ), writes=True, jail=jail, jail_login=target )
    assert rig.login.copies == [ target ] and len( jail.calls ) == 1
    rig.login.seconds = 1.0                               # a login too short to run: nothing is copied or started
    before = len( rig.login.copies )
    assert not rig.run( envelope( "x", "implement" ), writes=True, jail=RecordingJail(), jail_login=target ).ok
    assert len( rig.login.copies ) == before


def test_a_full_model_id_is_confirmed_from_the_cli_and_recorded() -> None:
    rig = Rig()
    os.environ[ "FAKE_CLAUDE_SERVED" ] = "claude-haiku-4-5-20251001,claude-opus-5"
    os.environ[ "FAKE_CLAUDE_INIT_MODEL" ] = "claude-opus-5"
    try:
        result = rig.run( envelope( "hi" ), model="claude-opus-5" )
    finally:
        del os.environ[ "FAKE_CLAUDE_SERVED" ], os.environ[ "FAKE_CLAUDE_INIT_MODEL" ]
    assert result.ok, result
    assert result.details[ "model_confirmed" ] is True and result.details[ "model_requested" ] == "claude-opus-5"
    assert result.details[ "models_used" ] == [ "claude-haiku-4-5-20251001", "claude-opus-5" ]


def test_a_task_never_quietly_runs_on_another_model() -> None:
    rig = Rig()
    os.environ[ "FAKE_CLAUDE_SERVED" ] = "claude-opus-4-8"
    os.environ[ "FAKE_CLAUDE_INIT_MODEL" ] = "claude-opus-4-8"
    try:
        result = rig.run( envelope( "hi" ), model="claude-opus-5" )
    finally:
        del os.environ[ "FAKE_CLAUDE_SERVED" ], os.environ[ "FAKE_CLAUDE_INIT_MODEL" ]
    assert not result.ok and result.details[ "error_source" ] == "model_mismatch"
    assert "claude-opus-4-8" in result.text and "ANSWER" not in result.text
    assert result.details[ "model_confirmed" ] is False


def test_an_alias_is_recorded_but_cannot_be_confirmed_by_name() -> None:
    rig    = Rig()
    result = rig.run( envelope( "hi" ), model="sonnet" )
    assert result.ok and result.details[ "model_confirmed" ] is None and result.details[ "model_requested" ] == "sonnet"


def test_the_shipped_claude_cards_pin_full_distinct_model_ids_and_separate_state() -> None:
    cards = { c[ "name" ]: c for c in json.loads(( Path( __file__ ).resolve().parents[ 1 ] / "agents.json" ).read_text())[ "agents" ]}
    sonnet, opus = cards[ "claude-sonnet" ][ "settings" ], cards[ "claude-opus" ][ "settings" ]
    assert sonnet[ "model" ].startswith( "claude-sonnet-" ) and opus[ "model" ].startswith( "claude-opus-" )
    assert sonnet[ "claude_home" ] != opus[ "claude_home" ] and ".claude-bridge" in opus[ "claude_home" ]
    assert sonnet[ "shell" ] is True and opus[ "shell" ] is True
    assert cards[ "claude-opus" ][ "accepts_from" ] == cards[ "claude-sonnet" ][ "accepts_from" ]
    assert [ c[ "task_type" ] for c in cards[ "claude-opus" ][ "capabilities" ]] == [ c[ "task_type" ] for c in cards[ "claude-sonnet" ][ "capabilities" ]]


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]

if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS ) } passed" )
