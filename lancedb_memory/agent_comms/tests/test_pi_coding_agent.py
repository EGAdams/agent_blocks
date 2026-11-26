"""PiCodingAgentExecutor (Sheila), offline: a fake `pi` script stands in for the CLI.

    cd lancedb_memory
    .venv/bin/python -m agent_comms.tests.test_pi_coding_agent
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_comms import factory
from agent_comms.executors.pi_coding_agent import (PiCodingAgentExecutor, PiLoginLifetime, READ_TOOLS, SHELL_TOOLS,
                                                   WRITE_TOOLS, progress_message, refresh_command)
from agent_comms.models import AgentCard, Envelope, JailUnavailable, MessageKind

FAKE = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[ 1: ]
mode = os.environ.get( "FAKE_PI_MODE", "ok" )
with open( os.environ[ "FAKE_PI_LOG" ], "w" ) as log:
    json.dump({ "args": args, "cwd": os.getcwd(), "stdin_closed": sys.stdin.read() == "" }, log )
def emit( event ):
    print( json.dumps( event ), flush=True )
if mode == "crash":
    print( "boom: not logged in", file=sys.stderr ); sys.exit( 3 )
served = os.environ.get( "FAKE_PI_SERVED", "openai-codex/gpt-6-sol" ).split( "/", 1 )
emit({ "type": "agent_start" })
emit({ "type": "tool_execution_start", "toolName": "read", "args": { "path": os.path.join( os.getcwd(), "notes.md" )}})
emit({ "type": "tool_execution_start", "toolName": "grep", "args": { "pattern": "def main" }})
answer = "ANSWER for: " + args[ -1 ].splitlines()[ -1 ]
if mode == "leak":
    answer = "token eyJabcdefghijk.abcdefghijkl.sig"
emit({ "type": "message_end", "message": { "role": "user", "content": [{ "type": "text", "text": "ignored" }]}})
emit({ "type": "message_end", "message": { "role": "assistant", "provider": served[ 0 ], "model": served[ 1 ],
       "stopReason": "error" if mode == "error" else "stop", "content": [{ "type": "thinking", "thinking": "secret plan" },
       { "type": "text", "text": answer }]}})
emit({ "type": "agent_end" })
"""


def envelope( text: str, task_type: str = "inspect", context: dict | None = None ) -> Envelope:
    return Envelope( id="t1", kind=MessageKind.REQUEST, sender="claude-code", recipient="sheila",
                     task_type=task_type, text=text, context=context or {}, correlation_id="t1" )


class FixedLifetime:
    def __init__( self, seconds: float ) -> None:
        self.seconds   = seconds
        self.refreshes = 0
        self.copies: list[ Path ] = []

    def seconds_left( self ) -> float:
        return self.seconds

    def refresh( self ) -> None:
        self.refreshes += 1

    def write_jail_copy( self, destination: Path ) -> None:
        self.copies.append( Path( destination ))


class RecordingJail:
    def __init__( self, fail: bool = False ) -> None:
        self.calls: list[ tuple ] = []
        self.fail = fail

    def wrap( self, argv, root, cwd, writable ):
        self.calls.append(( list( argv ), Path( root ), Path( cwd ), list( writable )))
        if self.fail:
            raise JailUnavailable( "bubblewrap is not installed" )
        return argv


class Rig:
    def __init__( self ) -> None:
        self.dir  = Path( tempfile.mkdtemp( prefix="pi_test_" ))
        self.work = self.dir / "work"
        self.work.mkdir()
        self.bin  = self.dir / "pi"
        self.bin.write_text( FAKE )
        self.bin.chmod( self.bin.stat().st_mode | stat.S_IEXEC )
        self.log  = self.dir / "log.json"
        self.progress: list[ tuple[ str, str, str ]] = []
        self.login = FixedLifetime( 8 * 3600 )
        os.environ[ "FAKE_PI_LOG" ] = str( self.log )
        os.environ.pop( "FAKE_PI_SERVED", None )

    def executor( self, jail=None, **kw ) -> PiCodingAgentExecutor:
        return PiCodingAgentExecutor( self.work, binary=str( self.bin ), timeout_seconds=20.0, grace_seconds=1.0,
                                      login=self.login, jail=jail or RecordingJail(), jail_login=self.dir / "jail.json",
                                      progress=lambda env, phase, message: self.progress.append(( env.id, phase, message )),
                                      **kw )

    def run( self, env: Envelope, mode: str = "ok", **kw ):
        os.environ[ "FAKE_PI_MODE" ] = mode
        return asyncio.run( self.executor( **kw ).execute( env ))

    def seen( self ) -> dict:
        return json.loads( self.log.read_text())


def test_answer_is_the_assistant_text_and_the_prompt_is_an_argument_not_a_shell() -> None:
    rig     = Rig()
    hostile = "explain; rm -rf / $(id)"
    result  = rig.run( envelope( hostile ))
    assert result.ok and result.text == "ANSWER for: " + hostile, result
    assert result.performer == "pi:read-only"
    seen = rig.seen()
    assert seen[ "stdin_closed" ] and seen[ "args" ][ -1 ].endswith( hostile )
    assert "secret plan" not in result.text


def test_the_model_is_pinned_and_confirmed_per_task() -> None:
    rig    = Rig()
    result = rig.run( envelope( "hi" ))
    args   = rig.seen()[ "args" ]
    assert args[ args.index( "--model" ) + 1 ] == "openai-codex/gpt-6-sol"
    assert result.details[ "model_confirmed" ] is True and result.details[ "models_used" ] == "openai-codex/gpt-6-sol"


def test_a_different_model_serving_the_task_fails_it_rather_than_passing() -> None:
    rig = Rig()
    os.environ[ "FAKE_PI_SERVED" ] = "google/gemini-x"
    result = rig.run( envelope( "hi" ))
    os.environ.pop( "FAKE_PI_SERVED" )
    assert not result.ok and "model_mismatch" in ( result.text + json.dumps( result.details )), result
    assert result.details[ "model_confirmed" ] is False


def test_tools_per_mode_and_a_reader_never_gets_bash() -> None:
    rig = Rig()
    rig.run( envelope( "x" ))
    args = rig.seen()[ "args" ]
    assert args[ args.index( "--tools" ) + 1 ] == ",".join( READ_TOOLS )
    assert not { "edit", "write", "bash" } & set( READ_TOOLS )
    rig.run( envelope( "x", "implement" ), writes=True )
    args = rig.seen()[ "args" ]
    assert args[ args.index( "--tools" ) + 1 ] == ",".join( WRITE_TOOLS ) and "bash" not in WRITE_TOOLS
    result = rig.run( envelope( "x", "implement" ), writes=True, shell=True )
    args   = rig.seen()[ "args" ]
    assert args[ args.index( "--tools" ) + 1 ] == ",".join( SHELL_TOOLS ) and result.performer == "pi:workspace-write+shell"
    for flag in ( "--no-extensions", "--no-skills", "--no-session", "--offline" ):
        assert flag in args


def test_construction_refuses_unsafe_combinations() -> None:
    for kwargs, word in (( dict( shell=True ), "shell" ), ( dict( writes=True, jail=None ), "jail" ),
                         ( dict( model="gpt-6-sol" ), "provider/id" ), ( dict( login=None ), "ILoginLifetime" )):
        base = dict( jail=RecordingJail(), login=FixedLifetime( 9999 ))
        base.update( kwargs )
        try:
            PiCodingAgentExecutor( Path( "/tmp" ), **base )
        except ValueError as error:
            assert word in str( error ), ( kwargs, error )
        else:
            raise AssertionError( f"{ kwargs } should be refused" )


def test_a_writer_is_jailed_to_its_workspace_and_a_reader_to_the_root_it_names() -> None:
    rig  = Rig()
    jail = RecordingJail()
    rig.run( envelope( "x", "implement" ), writes=True, jail=jail )
    _, root, cwd, _ = jail.calls[ 0 ]
    assert root == rig.work.resolve() and cwd == rig.work.resolve()
    other = rig.dir / "other"
    other.mkdir()
    jail = RecordingJail()
    rig.run( envelope( "x", context={ "workdir": str( other )}), jail=jail, read_roots=[ other ])
    _, root, cwd, _ = jail.calls[ 0 ]
    assert root == other.resolve()


def test_workdir_outside_the_roots_is_refused_without_running() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x", context={ "workdir": "/etc" }))
    assert not result.ok and "outside" in result.text and not rig.log.exists()


def test_unavailable_jail_fails_and_never_runs_unjailed() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x" ), jail=RecordingJail( fail=True ))
    assert not result.ok and "jail" in result.text and not rig.log.exists()


def test_a_crash_is_a_failed_task_with_the_exit_code() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x" ), mode="crash" )
    assert not result.ok and result.details.get( "exit_code" ) == 3, result


def test_a_credential_in_the_answer_is_withheld() -> None:
    rig    = Rig()
    result = rig.run( envelope( "x" ), mode="leak" )
    assert not result.ok and "eyJ" not in result.text and "eyJ" not in json.dumps( result.details )


def test_a_stale_login_is_refreshed_then_refused_without_running() -> None:
    rig = Rig()
    rig.login.seconds = 5
    result = rig.run( envelope( "x" ))
    assert rig.login.refreshes == 1 and not result.ok and "login" in result.text and not rig.log.exists()


def test_the_jail_login_copy_is_rewritten_before_each_run() -> None:
    rig = Rig()
    rig.run( envelope( "x" ))
    assert rig.login.copies == [ rig.dir / "jail.json" ]


def test_progress_reports_tool_calls_only() -> None:
    rig = Rig()
    rig.run( envelope( "find main" ))
    phases   = [ p for _, p, _ in rig.progress ]
    messages = " | ".join( m for _, _, m in rig.progress )
    assert phases[ 0 ] == "started" and phases[ -1 ] == "finished", rig.progress
    assert "notes.md" in messages and "def main" in messages
    assert "secret plan" not in messages and "find main" not in messages and "ANSWER" not in messages


def test_login_copy_drops_the_refresh_token_and_is_private() -> None:
    d    = Path( tempfile.mkdtemp())
    auth = d / "auth.json"
    auth.write_text( json.dumps({ "openai-codex": { "type": "oauth", "access": "A", "refresh": "R", "expires": 9e12 },
                                  "anthropic": { "access": "NOPE", "refresh": "NOPE2" }}))
    life = PiLoginLifetime( auth, "openai-codex", [ "true" ])
    dest = d / "sub" / "copy.json"
    life.write_jail_copy( dest )
    data = json.loads( dest.read_text())
    assert list( data ) == [ "openai-codex" ] and "refresh" not in data[ "openai-codex" ] and data[ "openai-codex" ][ "access" ] == "A"
    assert stat.S_IMODE( dest.stat().st_mode ) == 0o600
    assert "R" not in dest.read_text().replace( "oauth", "" ) and "NOPE" not in dest.read_text()
    assert life.seconds_left() > 1000
    assert PiLoginLifetime( d / "missing.json", "openai-codex", [ "true" ]).seconds_left() == 0.0


def test_refresh_command_is_tool_less_and_unsaved() -> None:
    cmd = refresh_command( "/x/pi", "openai-codex/gpt-6-sol" )
    assert cmd[ 0 ] == "/x/pi" and "--no-tools" in cmd and "--no-session" in cmd


def test_progress_message_names_the_target() -> None:
    event = { "type": "tool_execution_start", "toolName": "bash", "args": { "command": "ls -la" }}
    assert any( "ls -la" in m for _, m in progress_message( event, Path( "/w" )))
    assert progress_message({ "type": "message_end" }, Path( "/w" )) == []


def _sheila_card( home: Path, **over ) -> AgentCard:
    base = factory.JsonAgentRegistry( factory.REGISTRY ).card( "sheila" )
    settings = { **base.settings, "pi_home": str( home ), "jail_login_file": str( home.parent / "jail.json" ), **over }
    return base.model_copy( update={ "settings": settings })


def test_the_sheila_card_builds_a_reader_and_a_writer_per_capability() -> None:
    home    = Path( tempfile.mkdtemp()) / "pi-home"
    builder = factory.default_builders( None, None, Path( tempfile.mkdtemp()))[ "pi_coding_agent" ]
    routed  = builder( _sheila_card( home ))
    by_type = routed._executors
    assert by_type[ "implement" ].performer == "pi:workspace-write+shell"
    for reader in ( "inspect", "explain", "ask" ):
        assert by_type[ reader ].performer == "pi:read-only"
    assert ( home / "auth.json" ).stat().st_mode & 0o777 == 0o600


def test_the_sheila_card_refuses_the_real_pi_home() -> None:
    builder = factory.default_builders( None, None, Path( tempfile.mkdtemp()))[ "pi_coding_agent" ]
    source  = Path( tempfile.mkdtemp())
    try:
        builder( _sheila_card( source, pi_source_home=str( source )))
    except ValueError as error:
        assert "must not use" in str( error )
    else:
        raise AssertionError( "the card must not be allowed to share EG's Pi home" )


def test_the_sheila_card_is_not_offered_to_any_bridge_identity() -> None:
    card = _sheila_card( Path( tempfile.mkdtemp()))
    assert set( card.accepts_from ) == { "toyota", "sydney", "claude-code" }
    assert not [ s for s in card.accepts_from if s.endswith( "-bridge" )]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted( globals().items()):
        if name.startswith( "test_" ):
            try:
                fn(); print( "ok  ", name )
            except Exception as error:
                failed += 1; print( "FAIL", name, type( error ).__name__, error )
    sys.exit( 1 if failed else 0 )
