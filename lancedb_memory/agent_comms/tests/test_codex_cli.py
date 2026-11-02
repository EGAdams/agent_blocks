"""CodexCliExecutor, offline: a fake `codex` script stands in for the CLI.

    cd lancedb_memory
    .venv/bin/python -m agent_comms.tests.test_codex_cli
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

from agent_comms.executors.codex_cli import CodexCliExecutor
from agent_comms.models import Envelope, JailUnavailable, MessageKind

FAKE = """#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[ 1: ]
prompt = sys.stdin.read()
mode = os.environ.get( "FAKE_CODEX_MODE", "ok" )
out = args[ args.index( "-o" ) + 1 ]
with open( os.environ[ "FAKE_CODEX_LOG" ], "w" ) as log:
    json.dump({ "args": args, "prompt": prompt, "cwd": os.getcwd() }, log )
if mode == "sleep":
    time.sleep( 30 )
if mode == "fail":
    print( "boom: not logged in", file=sys.stderr ); sys.exit( 3 )
if mode != "silent":
    open( out, "w" ).write( "ANSWER for: " + prompt )
"""


def envelope( text: str, context: dict | None = None ) -> Envelope:
    return Envelope( id="t1", kind=MessageKind.REQUEST, sender="chatgpt-bridge", recipient="codex",
                     task_type="explain", text=text, context=context or {}, correlation_id="t1" )


class Rig:
    def __init__( self ) -> None:
        self.dir  = Path( tempfile.mkdtemp( prefix="codex_test_" ))
        self.work = self.dir / "work"
        ( self.work / "sub" ).mkdir( parents=True )
        self.bin  = self.dir / "codex"
        self.bin.write_text( FAKE )
        self.bin.chmod( self.bin.stat().st_mode | stat.S_IEXEC )
        self.log  = self.dir / "log.json"
        os.environ[ "FAKE_CODEX_LOG" ] = str( self.log )

    def run( self, env: Envelope, mode: str = "ok", timeout: float = 20.0 ):
        os.environ[ "FAKE_CODEX_MODE" ] = mode
        executor = CodexCliExecutor( self.work, binary=str( self.bin ), timeout_seconds=timeout, grace_seconds=1.0 )
        return asyncio.run( executor.execute( env ))

    def seen( self ) -> dict:
        return json.loads( self.log.read_text())


def test_answer_comes_from_outfile_and_prompt_from_stdin() -> None:
    rig    = Rig()
    hostile = "explain; rm -rf / --dangerously-bypass-approvals-and-sandbox $(id)"
    result = rig.run( envelope( hostile ))
    assert result.ok and result.text == "ANSWER for: " + hostile, result
    assert result.performer == "codex:read-only"
    seen = rig.seen()
    assert seen[ "prompt" ] == hostile
    assert hostile not in seen[ "args" ] and seen[ "args" ][ -1 ] == "-"
    assert seen[ "args" ][ seen[ "args" ].index( "--sandbox" ) + 1 ] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in seen[ "args" ]
    assert Path( seen[ "cwd" ]).resolve() == rig.work.resolve()


def test_subdirectory_workdir_is_allowed() -> None:
    rig    = Rig()
    result = rig.run( envelope( "hi", { "workdir": str( rig.work / "sub" )}))
    assert result.ok and Path( rig.seen()[ "cwd" ]).resolve() == ( rig.work / "sub" ).resolve()


def test_workdir_outside_root_is_refused_without_running() -> None:
    rig    = Rig()
    result = rig.run( envelope( "hi", { "workdir": str( rig.work / ".." / ".." )}))
    assert not result.ok and "outside" in result.text and not rig.log.exists()


def test_nonzero_exit_is_a_failure_with_stderr_tail() -> None:
    result = Rig().run( envelope( "hi" ), mode="fail" )
    assert not result.ok and "exited 3" in result.text and "not logged in" in result.text


def test_no_answer_is_a_failure() -> None:
    result = Rig().run( envelope( "hi" ), mode="silent" )
    assert not result.ok and "no answer" in result.text


def test_timeout_kills_the_group() -> None:
    result = Rig().run( envelope( "hi" ), mode="sleep", timeout=1.0 )
    assert not result.ok and result.details.get( "timed_out" ) is True


def test_missing_binary_is_a_message_not_a_traceback() -> None:
    executor = CodexCliExecutor( Path( tempfile.mkdtemp()), binary="/nonexistent/codex" )
    result   = asyncio.run( executor.execute( envelope( "hi" )))
    assert not result.ok and "could not start codex" in result.text


def test_card_model_is_passed_and_checked() -> None:
    work = Path( tempfile.mkdtemp())
    argv = CodexCliExecutor( work, model="gpt-5.6-luna" ).argv( work, work / "out.txt" )
    assert argv[ argv.index( "-m" ) + 1 ] == "gpt-5.6-luna" and "read-only" in argv
    assert "-m" not in CodexCliExecutor( work ).argv( work, work / "out.txt" )
    try:
        CodexCliExecutor( work, model="x; rm -rf /" )
    except ValueError:
        return
    raise AssertionError( "a model name with shell characters must be refused" )


class RecordingJail:
    def __init__( self, refuse: bool = False ) -> None:
        self.refuse, self.calls = refuse, []

    def wrap( self, argv: list[ str ], root: Path, cwd: Path, writable: list[ Path ]) -> list[ str ]:
        if self.refuse:
            raise JailUnavailable( "bubblewrap is not installed" )
        self.calls.append(( root, cwd, writable ))
        return argv


def test_jail_wraps_the_command_with_root_cwd_and_scratch() -> None:
    rig, jail = Rig(), RecordingJail()
    os.environ[ "FAKE_CODEX_MODE" ] = "ok"
    executor = CodexCliExecutor( rig.work, binary=str( rig.bin ), grace_seconds=1.0, jail=jail )
    result   = asyncio.run( executor.execute( envelope( "hi", { "workdir": str( rig.work / "sub" )})))
    root, cwd, writable = jail.calls[ 0 ]
    assert result.ok and root == rig.work.resolve() and cwd == ( rig.work / "sub" ).resolve()
    assert writable[ 0 ] == Path( rig.seen()[ "args" ][ rig.seen()[ "args" ].index( "-o" ) + 1 ]).parent


def test_unavailable_jail_means_codex_never_starts() -> None:
    rig      = Rig()
    executor = CodexCliExecutor( rig.work, binary=str( rig.bin ), jail=RecordingJail( refuse=True ))
    result   = asyncio.run( executor.execute( envelope( "hi" )))
    assert not result.ok and "jail is unavailable" in result.text and not rig.log.exists()


def test_workspace_write_is_only_what_it_was_built_as() -> None:
    rig, jail = Rig(), RecordingJail()
    os.environ[ "FAKE_CODEX_MODE" ] = "ok"
    executor = CodexCliExecutor( rig.work, binary=str( rig.bin ), grace_seconds=1.0, jail=jail, sandbox="workspace-write" )
    result   = asyncio.run( executor.execute( envelope( "make notes.md", { "workdir": str( rig.work / "sub" )})))
    seen     = rig.seen()
    assert result.ok and result.performer == "codex:workspace-write"
    assert seen[ "args" ][ seen[ "args" ].index( "--sandbox" ) + 1 ] == "workspace-write"
    assert "--dangerously-bypass-approvals-and-sandbox" not in seen[ "args" ]
    assert seen[ "prompt" ].endswith( "make notes.md" ) and "Do not run git commit" in seen[ "prompt" ]
    root, cwd, _ = jail.calls[ 0 ]
    assert root == cwd == ( rig.work / "sub" ).resolve(), "the jail's writable root is the one workspace, not the card's"
    read_only = CodexCliExecutor( rig.work, binary=str( rig.bin ), grace_seconds=1.0 )
    asyncio.run( read_only.execute( envelope( "please write a file anyway" )))
    assert rig.seen()[ "prompt" ] == "please write a file anyway", "a read-only task gets no write rules"


def test_write_mode_needs_a_jail_and_a_known_sandbox() -> None:
    work = Path( tempfile.mkdtemp())
    for kw in ({ "sandbox": "workspace-write" }, { "sandbox": "danger-full-access", "jail": RecordingJail()}):
        try:
            CodexCliExecutor( work, **kw )
        except ValueError:
            continue
        raise AssertionError( f"{ kw } must be refused" )


def test_answer_carrying_the_login_is_withheld() -> None:
    rig  = Rig()
    auth = rig.dir / "auth.json"
    auth.write_text( json.dumps({ "tokens": { "refresh_token": "rt_FAKEFAKEFAKEFAKEFAKEFAKE1234" }}))
    os.environ[ "FAKE_CODEX_MODE" ] = "ok"
    executor = CodexCliExecutor( rig.work, binary=str( rig.bin ), grace_seconds=1.0, secret_files=[ auth ])
    leaked   = asyncio.run( executor.execute( envelope( "here: rt_FAKEFAKEFAKEFAKEFAKEFAKE1234" )))
    assert not leaked.ok and "withheld" in leaked.text and "rt_FAKE" not in leaked.text
    jwt      = asyncio.run( executor.execute( envelope( "eyJhbGciOiJSUzI1.eyJzdWIiOiIxMjM0.sig" )))
    assert not jwt.ok and "withheld" in jwt.text
    assert asyncio.run( executor.execute( envelope( "explain the voice system" ))).ok


def test_read_root_is_jailed_to_itself_and_others_stay_refused() -> None:
    rig, jail = Rig(), RecordingJail()
    other     = rig.dir / "other_project"
    ( other / "src" ).mkdir( parents=True )
    os.environ[ "FAKE_CODEX_MODE" ] = "ok"
    executor = CodexCliExecutor( rig.work, binary=str( rig.bin ), grace_seconds=1.0, jail=jail,
                                 read_roots=[ other ])
    result   = asyncio.run( executor.execute( envelope( "hi", { "workdir": str( other / "src" )})))
    root, cwd, _ = jail.calls[ 0 ]
    assert result.ok and root == other.resolve() and cwd == ( other / "src" ).resolve()
    asyncio.run( executor.execute( envelope( "hi" )))
    assert jail.calls[ 1 ][ 0 ] == rig.work.resolve()
    refused  = asyncio.run( executor.execute( envelope( "hi", { "workdir": str( rig.dir )})))
    assert not refused.ok and "outside" in refused.text and len( jail.calls ) == 2


def test_a_writer_is_jailed_to_the_write_root_it_is_pointed_at() -> None:
    rig, jail = Rig(), RecordingJail()
    project   = rig.dir / "project"
    project.mkdir()
    os.environ[ "FAKE_CODEX_MODE" ] = "ok"
    executor = CodexCliExecutor( rig.work, binary=str( rig.bin ), grace_seconds=1.0, jail=jail,
                                 sandbox="workspace-write", write_roots=[ project ])
    result   = asyncio.run( executor.execute( envelope( "hi", { "workdir": str( project )})))
    root, cwd, _ = jail.calls[ 0 ]
    assert result.ok and root == project.resolve() and cwd == project.resolve()
    refused  = asyncio.run( executor.execute( envelope( "hi", { "workdir": str( rig.dir )})))
    assert not refused.ok and "outside" in refused.text and len( jail.calls ) == 1


def test_a_reader_takes_no_write_roots() -> None:
    work = Path( tempfile.mkdtemp())
    try:
        CodexCliExecutor( work, jail=RecordingJail(), write_roots=[ work.parent ])
    except ValueError:
        return
    raise AssertionError( "a read-only executor must refuse write_roots" )


def test_a_writer_takes_no_read_roots() -> None:
    work = Path( tempfile.mkdtemp())
    try:
        CodexCliExecutor( work, jail=RecordingJail(), sandbox="workspace-write", read_roots=[ work.parent ])
    except ValueError:
        return
    raise AssertionError( "a write-enabled executor must refuse read_roots" )


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]

if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS ) } passed" )
