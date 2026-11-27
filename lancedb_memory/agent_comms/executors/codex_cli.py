"""CodexCliExecutor -- ITaskExecutor over `codex exec`, read-only unless built otherwise.

Codex CLI is the bridge's first agent. The request text goes to codex on
stdin, never on the command line, so nothing in it can become an argument.
The sandbox is chosen when the executor is built -- `read-only` for the
`codex_cli` runtime, `workspace-write` only for `codex_cli_write` -- and no
envelope field can change it; `--dangerously-bypass-approvals-and-sandbox`
is never passed. A workspace-write executor jails codex to the one workspace
it was asked to work in (not the card's whole workdir), so the writable area
is exactly that directory. The answer is the file codex writes with `-o` (its last
message), not its progress chatter on stdout.

`--json` events are read as they arrive and reported through `progress`:
commands, their output and exit codes, file changes, tool calls, Codex's own
visible messages, and stderr. Reasoning items are never read out. A
`turn.failed` event fails the task even when codex then exits 0.

Codex's sandbox stops writes, not reads, so a card may also name a jail
(`ICommandJail`, see jail.py) that limits what codex can see to the card's
workdir minus its private data. A jail that cannot be built fails the task;
codex is never started without it.

A read-only card may also name `read_roots`: further project directories a
request may be pointed at (the bridge's `letta_code` workspace is one). A
request inside one of them is jailed to that root alone, so it sees that
project and not the card's workdir. A write-enabled card names `write_roots`
instead (letta_code for codex-writer): the request's workspace may be one of
them, and the jail is still built on that one directory, so the writable area
stays the one workspace it was given.

Codex has to be able to read its own login, so a prompt could ask it to
print it. `secret_files` names those files (the bridge home's auth.json);
any answer or error text carrying one of their values, or anything shaped
like a JWT, is withheld rather than returned.

A timeout ends codex's whole process group, TERM then KILL, the same way
AdwSubprocessExecutor ends an ADW.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..live_output import MAX_OUTPUT
from ..models import Envelope, JailUnavailable, TaskResult
from ..outcome import OUTCOME_RULE
from ..ports import ICommandJail

JWT     = re.compile( r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\." )
WITHHELD = "codex's answer was withheld: it contained login credentials"
SANDBOXES = ( "read-only", "workspace-write" )

# Said to codex on every write task. The sandbox and jail enforce the boundary;
# this only keeps codex from wasting the task trying to cross it.
WRITE_RULES = """You are working in a dedicated workspace on EG's machine, through the ChatGPT bridge.
You may create and edit files in the current directory only. Do not run git commit, push, reset,
checkout, clean or stash; do not delete files you did not create in this task; do not try to reach
the network. Finish with a short summary of the files you created or changed and any checks you ran.
""" + OUTCOME_RULE + """
Task:
"""


Progress = Callable[[ Envelope, str, str ], None ]       # (task, phase, message)
OUTPUT_TAIL = 8                                          # lines of one command's output shown


def codex_steps( event: dict[ str, Any ]) -> list[ tuple[ str, str ]]:
    """The (phase, message) steps one `--json` event shows. Reasoning is private and never listed."""
    kind = str( event.get( "type", "" ))
    if kind == "turn.failed":
        return [( "error", f"turn failed: { ( event.get( 'error' ) or {}).get( 'message', 'no reason given' )}" )]
    if kind == "error":
        return [( "error", str( event.get( "message", "codex reported an error" )))]
    if kind == "turn.completed":
        return [( "tool", "turn completed" )]
    item = event.get( "item" ) or {}
    what, done = item.get( "type" ), kind == "item.completed"
    if what == "command_execution":
        if not done:
            return [( "command", f"$ { item.get( 'command', '' )}" )] if kind == "item.started" else []
        tail = [ line for line in str( item.get( "aggregated_output", "" )).splitlines() if line.strip()]
        return [*[( "output", line ) for line in tail[ -OUTPUT_TAIL: ]],
                ( "tool", f"command finished, exit { item.get( 'exit_code' )}" )]
    if not done:
        return []
    if what == "file_change":
        return [( "tool", f"file { c.get( 'kind', 'changed' )}: { c.get( 'path', '?' )}" ) for c in item.get( "changes" ) or []]
    if what == "mcp_tool_call":
        return [( "tool", f"tool { item.get( 'server', '' )}.{ item.get( 'tool', '' )}: { item.get( 'status', '' )}" )]
    if what == "web_search":
        return [( "tool", "web search" )]
    if what == "agent_message":
        return [( "message", str( item.get( "text", "" )))]
    if what == "error":
        return [( "error", str( item.get( "message", "codex reported an error" )))]
    return []


class CodexCliExecutor:

    def __init__( self, workdir: Path, binary: str = "codex", timeout_seconds: float = 900.0,
                  grace_seconds: float = 10.0, model: str | None = None,
                  jail: ICommandJail | None = None, secret_files: list[ Path ] | None = None,
                  sandbox: str = "read-only", read_roots: list[ Path ] | None = None,
                  write_roots: list[ Path ] | None = None, progress: Progress | None = None ) -> None:
        if sandbox not in SANDBOXES:
            raise ValueError( f"unknown codex sandbox { sandbox!r}" )
        if read_roots and sandbox != "read-only":
            raise ValueError( "read_roots are for read-only codex only; a writer names write_roots" )
        if write_roots and sandbox == "read-only":
            raise ValueError( "write_roots are for a write-enabled codex only" )
        if sandbox != "read-only" and jail is None:
            raise ValueError( "a write-enabled codex executor needs a jail; it never runs unconfined" )
        self._sandbox = sandbox
        if model is not None and not re.fullmatch( r"[A-Za-z0-9._-]{1,64}", model ):
            raise ValueError( "codex model must be a plain model name" )
        self._model   = model
        self._workdir = Path( workdir )
        self._roots   = [ Path( workdir ), *[ Path( r ) for r in [ *( read_roots or []), *( write_roots or [])]]]
        self._binary  = binary
        self._timeout = timeout_seconds
        self._grace   = grace_seconds
        self._jail    = jail
        self._secrets = [ Path( f ) for f in secret_files or []]
        self._progress = progress

    @property
    def performer( self ) -> str:
        return f"codex:{ self._sandbox }"

    def argv( self, workdir: Path, outfile: Path ) -> list[ str ]:
        model = [ "-m", self._model ] if self._model else []
        return [ self._binary, "exec", "--sandbox", self._sandbox, *model, "--cd", str( workdir ),
                 "--ephemeral", "--skip-git-repo-check", "--color", "never", "--json", "-o", str( outfile ), "-" ]

    async def execute( self, envelope: Envelope ) -> TaskResult:
        return await asyncio.to_thread( self._run, envelope )

    def _workdir_for( self, envelope: Envelope ) -> tuple[ Path, Path ]:
        """( root, workdir ): a request may name a directory inside one of the card's roots, never outside them."""
        asked = envelope.context.get( "workdir" )
        if not asked:
            return self._workdir.resolve(), self._workdir
        target = Path( str( asked )).resolve()
        for root in ( r.resolve() for r in self._roots ):
            if target == root or root in target.parents:
                return root, target
        raise PermissionError( f"workdir { asked } is outside { ', '.join( str( r ) for r in self._roots ) }" )

    def _run( self, envelope: Envelope ) -> TaskResult:
        try:
            root, workdir = self._workdir_for( envelope )
        except PermissionError as error:
            return TaskResult( ok=False, text=str( error ), performer=self.performer )
        with tempfile.TemporaryDirectory( prefix="codex_out_" ) as scratch:
            outfile = Path( scratch ) / "last_message.txt"
            try:
                command = self._jailed( self.argv( workdir, outfile ), root, workdir, Path( scratch ))
            except JailUnavailable as error:
                return TaskResult( ok=False, text=f"codex not started, its jail is unavailable: { error }",
                                   performer=self.performer )
            try:
                process = subprocess.Popen( command, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                            cwd=str( workdir ), start_new_session=True )
            except OSError as error:
                return TaskResult( ok=False, text=f"could not start codex: { error }", performer=self.performer )
            self._emit( envelope, "started", f"codex started ({ self._sandbox }) in { workdir.name }, pid={ process.pid }" )
            stderr, failure, timed_out = self._stream( envelope, process )
            if timed_out:
                self._emit( envelope, "finished", f"stopped after { round( self._timeout )}s without finishing" )
                return TaskResult( ok=False, text=f"codex did not finish within { round( self._timeout ) }s",
                                   performer=self.performer, details={ "exit_code": None, "timed_out": True })
            self._emit( envelope, "finished", f"codex exited { process.returncode }" )
            answer = outfile.read_text( encoding="utf-8" ).strip() if outfile.exists() else ""
        if self._leaks( answer ) or self._leaks( stderr or "" ):
            return TaskResult( ok=False, text=WITHHELD, performer=self.performer,
                               details={ "exit_code": process.returncode, "withheld": True })
        if process.returncode != 0:
            tail = ( stderr or "" ).strip().splitlines()[ -3: ]
            return TaskResult( ok=False, text=f"codex exited { process.returncode }: { ' | '.join( tail ) or 'no error text' }",
                               performer=self.performer, details={ "exit_code": process.returncode })
        if failure:
            return TaskResult( ok=False, text=f"codex's turn failed: { failure }", performer=self.performer,
                               details={ "exit_code": 0 })
        if not answer:
            return TaskResult( ok=False, text="codex finished but wrote no answer", performer=self.performer,
                               details={ "exit_code": 0 })
        return TaskResult( ok=True, text=answer, performer=self.performer, details={ "exit_code": 0 })

    def _emit( self, envelope: Envelope, phase: str, message: str ) -> None:
        if self._progress is None:
            return
        if self._leaks( message ):
            message = "[withheld: it carried a login value]"
        try:
            self._progress( envelope, phase, message )
        except Exception:                                    # noqa: BLE001 -- showing a step never stops the task
            pass

    def _stream( self, envelope: Envelope, process: subprocess.Popen ) -> tuple[ str, str, bool ]:
        """( stderr, turn failure, timed out ). Both pipes are read as they fill, so each line shows at once."""
        expired = threading.Event()
        errors: list[ str ] = []
        shown = [ 0 ]

        def show( phase: str, message: str ) -> None:
            shown[ 0 ] += 1
            if shown[ 0 ] <= MAX_OUTPUT:
                self._emit( envelope, phase, message )

        def read_errors() -> None:
            for line in process.stderr:
                errors.append( line )
                if line.strip():
                    show( "stderr", line.rstrip())

        def stop() -> None:
            expired.set()
            for sig in ( signal.SIGTERM, signal.SIGKILL ):
                try:
                    os.killpg( process.pid, sig )
                except ProcessLookupError:
                    return
                try:
                    process.wait( timeout=self._grace )
                except subprocess.TimeoutExpired:
                    continue

        reader = threading.Thread( target=read_errors, daemon=True )
        timer  = threading.Timer( self._timeout, stop )
        timer.daemon = True
        reader.start()
        timer.start()
        failure = ""
        try:
            try:
                process.stdin.write( self._prompt( envelope ))
                process.stdin.close()
            except ( BrokenPipeError, OSError ):
                pass
            for line in process.stdout:
                try:
                    event = json.loads( line )
                except ValueError:
                    if line.strip():
                        show( "output", line.rstrip())
                    continue
                if not isinstance( event, dict ):
                    continue
                if event.get( "type" ) == "turn.failed":
                    failure = str(( event.get( "error" ) or {}).get( "message" ) or "no reason given" )
                elif event.get( "type" ) == "turn.completed":
                    failure = ""
                for phase, message in codex_steps( event ):
                    show( phase, message )
            process.wait()
            reader.join( timeout=self._grace )
        finally:
            timer.cancel()
        if shown[ 0 ] > MAX_OUTPUT:
            self._emit( envelope, "tool", f"{ shown[ 0 ] - MAX_OUTPUT } further line(s) not listed" )
        return "".join( errors ), failure, expired.is_set()

    def _prompt( self, envelope: Envelope ) -> str:
        return envelope.text if self._sandbox == "read-only" else WRITE_RULES + envelope.text

    def _leaks( self, text: str ) -> bool:
        if not text:
            return False
        if JWT.search( text ):
            return True
        return any( value in text for value in self._secret_values())

    def _secret_values( self ) -> set[ str ]:
        """Every long string in the secret files, read fresh: the login rotates."""
        values: set[ str ] = set()
        for path in self._secrets:
            try:
                data = json.loads( path.read_text( encoding="utf-8" ))
            except ( OSError, ValueError ):
                continue
            stack = [ data ]
            while stack:
                item = stack.pop()
                if isinstance( item, dict ):
                    stack += list( item.values())
                elif isinstance( item, list ):
                    stack += item
                elif isinstance( item, str ) and len( item ) >= 24:
                    values.add( item )
        return values

    def _jailed( self, argv: list[ str ], root: Path, workdir: Path, scratch: Path ) -> list[ str ]:
        if self._jail is None:
            return argv
        root = root if self._sandbox == "read-only" else workdir
        return self._jail.wrap( argv, root, workdir, [ scratch ])
