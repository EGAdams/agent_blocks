"""ClaudeCliExecutor -- ITaskExecutor over `claude -p`, jailed like Codex.

The Claude Code CLI already on this machine is signed in with EG's claude.ai
subscription. This executor reuses that login and nothing else: no API key,
no second account, no copy of the login file. Inside the jail (BwrapJail,
see jail.py) Claude gets a dedicated config directory (~/.claude-bridge, the
counterpart of ~/.codex-bridge) and the one login file bound into it
**read-only**, so EG's ~/.claude -- his conversations, history and settings --
is not there, and nothing in the jail can rewrite or rotate his login.

Because the jailed Claude cannot save a refreshed login, the login's life is
checked before each run (ILoginLifetime). Close to expiry it is refreshed
outside the jail through the ordinary CLI, the way EG's own Claude use
refreshes it, and the run's time limit is cut to what the login has left. A
login that cannot cover a short run fails the task before Claude starts.

What Claude may do is fixed when the executor is built, never by the request:

  * read-only: the Read, Glob and Grep tools, jailed to the root holding the
    requested directory (the card's workdir or one of its `read_roots`);
  * write (`writes=True`, the `implement` operation): Read, Glob, Grep, Edit
    and Write, jailed to the one workspace it was given, which is bound
    writable with its `.git` still read-only. A writer must have a jail.

Bash, web access, sub-agents and MCP servers are never offered, so a task can
change files but cannot run commands or reach anything but Anthropic. The
worker's own VerifyChanges phase runs the workspace's checks afterwards.

The request goes on stdin, never the command line. Claude's `stream-json`
output is read as it arrives: every tool call it really makes is reported
through `progress` as it happens (tool name and the file or pattern only --
never the prompt, the reply, or Claude's own text), and the final `result`
event is the answer. Nothing is reported that Claude did not do. Any answer
or error text carrying a login value, or shaped like a JWT, is withheld.
A timeout ends Claude's whole process group, TERM then KILL.
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
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..models import Envelope, JailUnavailable, TaskResult
from ..ports import ICommandJail

READ_TOOLS   = ( "Read", "Glob", "Grep" )
WRITE_TOOLS  = ( "Read", "Glob", "Grep", "Edit", "Write" )
NEVER_TOOLS  = ( "Bash", "WebFetch", "WebSearch", "Task", "NotebookEdit" )
JWT          = re.compile( r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\." )
WITHHELD     = "Claude's answer was withheld: it contained login credentials"
LOGIN_MARGIN = 60.0          # seconds of login kept back beyond the run's end
SHORTEST_RUN = 120.0         # a login that cannot cover this long a run fails the task
MAX_PROGRESS = 60            # progress events per task; the rest are counted, not sent
CLIP         = 160

READ_RULES = """You are answering through the ChatGPT bridge on EG's machine, read-only.
Use the Read, Glob and Grep tools on the current directory; do not try to change anything.
Answer plainly and name the files you relied on.

Task:
"""

# Said on every write task. The jail and the tool list enforce the boundary;
# this only keeps Claude from wasting the task trying to cross it.
WRITE_RULES = """You are working in a dedicated workspace on EG's machine, through the ChatGPT bridge.
You may create and edit files in the current directory only, with the Edit and Write tools. You cannot
run commands; the bridge runs the workspace's checks after you finish. Do not delete files you did not
create in this task. Finish with a short summary of the files you created or changed.

Task:
"""

Progress = Callable[[ Envelope, str, str ], None ]       # (task, phase, message)


class LoginStale( RuntimeError ):
    """The login cannot cover even a short run; Claude is not started."""


class ILoginLifetime( Protocol ):
    def seconds_left( self ) -> float: ...
    def refresh( self ) -> None: ...


class ClaudeLoginLifetime:
    """ILoginLifetime over Claude Code's own login file. Reads the expiry only, never logs a value."""

    def __init__( self, credentials: Path, refresh_command: list[ str ], timeout_seconds: float = 90.0 ) -> None:
        self._file    = Path( credentials )
        self._command = list( refresh_command )
        self._timeout = timeout_seconds

    def seconds_left( self ) -> float:
        try:
            oauth = json.loads( self._file.read_text( encoding="utf-8" )).get( "claudeAiOauth" ) or {}
            return float( oauth.get( "expiresAt", 0 )) / 1000.0 - time.time()
        except ( OSError, ValueError, TypeError, AttributeError ):
            return 0.0

    def refresh( self ) -> None:
        """One tool-less, unsaved CLI turn outside the jail: Claude Code renews its own login as it starts."""
        with tempfile.TemporaryDirectory( prefix="claude_login_" ) as empty:
            try:
                subprocess.run( self._command, input="Reply with OK.", text=True, capture_output=True, cwd=empty,
                                timeout=self._timeout, env=clean_env(), start_new_session=True )
            except ( OSError, subprocess.TimeoutExpired ):
                pass                        # judged by seconds_left() afterwards, not by this exit


def refresh_command( binary: str ) -> list[ str ]:
    return [ binary, "-p", "--model", "haiku", "--tools", "", "--max-turns", "1",
             "--strict-mcp-config", "--no-session-persistence" ]


def clean_env() -> dict[ str, str ]:
    """This process's environment without any Anthropic key or endpoint: the subscription login only."""
    return { k: v for k, v in os.environ.items() if not k.startswith( "ANTHROPIC_" )}


def _clip( text: str ) -> str:
    flat = " ".join( str( text ).split())
    return flat if len( flat ) <= CLIP else flat[ :CLIP - 1 ] + "…"


def _target( tool_input: dict[ str, Any ], root: Path ) -> str:
    for key in ( "file_path", "path", "notebook_path" ):
        value = tool_input.get( key )
        if isinstance( value, str ) and value:
            path = Path( value )
            try:
                return str( path.resolve().relative_to( root.resolve()))
            except ( ValueError, OSError ):
                return path.name
    pattern = tool_input.get( "pattern" )
    return f'"{ _clip( pattern )[ :60 ]}"' if isinstance( pattern, str ) and pattern else ""


def progress_message( event: dict[ str, Any ], root: Path ) -> list[ tuple[ str, str ]]:
    """The (phase, message) steps one stream-json event shows. Text, prompts and replies are never included."""
    kind = event.get( "type" )
    if kind == "system" and event.get( "subtype" ) == "init":
        source = event.get( "apiKeySource" )
        login  = "subscription login" if source in ( None, "none" ) else f"login source { source }"
        return [( "session", _clip( f"Claude { event.get( 'model', '' )} session open ({ login })" ))]
    content = ( event.get( "message" ) or {}).get( "content" ) or []
    steps: list[ tuple[ str, str ]] = []
    for part in content if isinstance( content, list ) else []:
        if not isinstance( part, dict ):
            continue
        if kind == "assistant" and part.get( "type" ) == "tool_use":
            target = _target( part.get( "input" ) or {}, root )
            steps.append(( "tool", _clip( f"{ part.get( 'name', 'tool' )} { target }".strip())))
        elif kind == "user" and part.get( "type" ) == "tool_result" and part.get( "is_error" ):
            steps.append(( "tool_error", "a tool call failed; Claude continues" ))
    return steps


class ClaudeCliExecutor:

    def __init__( self, workdir: Path, binary: str = "claude", model: str = "sonnet", timeout_seconds: float = 900.0,
                  grace_seconds: float = 10.0, max_turns: int = 40, writes: bool = False,
                  jail: ICommandJail | None = None, login: ILoginLifetime | None = None,
                  secret_files: list[ Path ] | None = None, read_roots: list[ Path ] | None = None,
                  write_roots: list[ Path ] | None = None, progress: Progress | None = None ) -> None:
        if writes and jail is None:
            raise ValueError( "a write-enabled claude executor needs a jail; it never runs unconfined" )
        if read_roots and writes:
            raise ValueError( "read_roots are for a read-only claude; a writer names write_roots" )
        if write_roots and not writes:
            raise ValueError( "write_roots are for a write-enabled claude only" )
        if not re.fullmatch( r"[A-Za-z0-9._-]{1,64}", model ):
            raise ValueError( "claude model must be a plain model name" )
        if login is None:
            raise ValueError( "a claude executor needs an ILoginLifetime" )
        self._workdir  = Path( workdir )
        self._roots    = [ Path( workdir ), *[ Path( r ) for r in [ *( read_roots or []), *( write_roots or [])]]]
        self._binary   = binary
        self._model    = model
        self._timeout  = timeout_seconds
        self._grace    = grace_seconds
        self._turns    = int( max_turns )
        self._writes   = writes
        self._jail     = jail
        self._login    = login
        self._secrets  = [ Path( f ) for f in secret_files or []]
        self._progress = progress

    @property
    def performer( self ) -> str:
        return "claude:workspace-write" if self._writes else "claude:read-only"

    def argv( self ) -> list[ str ]:
        tools = ",".join( WRITE_TOOLS if self._writes else READ_TOOLS )
        return [ self._binary, "-p", "--model", self._model, "--output-format", "stream-json", "--verbose",
                 "--max-turns", str( self._turns ), "--tools", tools, "--allowedTools", tools,
                 "--disallowedTools", ",".join( NEVER_TOOLS ), "--strict-mcp-config", "--no-session-persistence" ]

    def run_limit( self ) -> float:
        """Seconds this run may take: the card's limit, cut to what the login has left."""
        left = self._login.seconds_left()
        if left < self._timeout + LOGIN_MARGIN:
            self._login.refresh()
            left = self._login.seconds_left()
        limit = min( self._timeout, left - LOGIN_MARGIN )
        if limit < min( SHORTEST_RUN, self._timeout ):
            raise LoginStale( "Claude's subscription login has expired or is about to, and renewing it failed; "
                              "sign in again with `claude` on this machine" )
        return limit

    async def execute( self, envelope: Envelope ) -> TaskResult:
        return await asyncio.to_thread( self._run, envelope )

    def _workdir_for( self, envelope: Envelope ) -> tuple[ Path, Path ]:
        """( root, workdir ): a request may name a directory inside one of the card's roots, never outside them."""
        asked = envelope.context.get( "workdir" )
        if not asked:
            return self._workdir.resolve(), self._workdir.resolve()
        target = Path( str( asked )).resolve()
        for root in ( r.resolve() for r in self._roots ):
            if target == root or root in target.parents:
                return root, target
        raise PermissionError( f"workdir { asked } is outside { ', '.join( str( r ) for r in self._roots ) }" )

    def _fail( self, text: str, **details: Any ) -> TaskResult:
        return TaskResult( ok=False, text=text, performer=self.performer, details=details )

    def _run( self, envelope: Envelope ) -> TaskResult:
        try:
            root, workdir = self._workdir_for( envelope )
        except PermissionError as error:
            return self._fail( str( error ))
        try:
            limit = self.run_limit()
        except LoginStale as error:
            return self._fail( str( error ))
        with tempfile.TemporaryDirectory( prefix="claude_out_" ) as scratch:
            try:
                command = self._jailed( self.argv(), root, workdir, Path( scratch ))
            except JailUnavailable as error:
                return self._fail( f"claude not started, its jail is unavailable: { error }" )
            with open( Path( scratch ) / "stderr.txt", "w+", encoding="utf-8", errors="replace" ) as errors:
                try:
                    process = subprocess.Popen( command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                                text=True, cwd=str( workdir ), start_new_session=True, env=clean_env())
                except OSError as error:
                    return self._fail( f"could not start claude: { error }" )
                self._emit( envelope, "started", f"Claude ({ self._model }) started on { envelope.task_type } in "
                                                 f"{ workdir.name } ({ self.performer.split( ':' )[ 1 ]}); pid={ process.pid }, "
                                                 f"max_turns={ self._turns }, timeout={ limit }s" )
                final, timed_out = self._stream( envelope, process, workdir, limit )
                errors.seek( 0 )
                stderr = errors.read()
        result = self._result( envelope, process.returncode, final, timed_out, stderr, limit )
        return result.model_copy( update={ "details": {
            **result.details, "model": self._model, "configured_max_turns": self._turns,
            "timeout_seconds": limit, "child_pid": process.pid, "process_group_id": process.pid,
            "bridge_task_id": envelope.context.get( "bridge_task_id" ), "local_task_id": envelope.id,
        }})

    def _stream( self, envelope: Envelope, process: subprocess.Popen, workdir: Path,
                 limit: float ) -> tuple[ dict | None, bool ]:
        expired = threading.Event()

        def stop() -> None:
            expired.set()
            self._end_group( process )

        timer = threading.Timer( limit, stop )
        timer.daemon = True
        timer.start()
        final: dict | None = None
        sent = hidden = 0
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
                    continue
                if not isinstance( event, dict ):
                    continue
                if event.get( "type" ) == "result":
                    final = event
                    continue
                for phase, message in progress_message( event, workdir ):
                    if sent < MAX_PROGRESS:
                        self._emit( envelope, phase, message )
                        sent += 1
                    else:
                        hidden += 1
            process.wait()
        finally:
            timer.cancel()
        if hidden:
            self._emit( envelope, "tool", f"{ hidden } further step(s) not listed" )
        return final, expired.is_set()

    def _result( self, envelope: Envelope, code: int | None, final: dict | None, timed_out: bool,
                 stderr: str, limit: float ) -> TaskResult:
        answer = str(( final or {}).get( "result" ) or "" ).strip()
        if self._leaks( answer ) or self._leaks( stderr ):
            self._emit( envelope, "finished", "Claude finished; its answer was withheld (credential-shaped text)" )
            return self._fail( WITHHELD, exit_code=code, withheld=True )
        if timed_out:
            self._emit( envelope, "finished", f"stopped after { round( limit )}s without finishing" )
            return self._fail( f"claude did not finish within { round( limit )}s", exit_code=None, timed_out=True )
        if final is not None:
            secs = round( float( final.get( "duration_ms" ) or 0 ) / 1000, 1 )
            how  = final.get( "subtype" ) or "done"
            self._emit( envelope, "finished", f"Claude finished: { how }, { final.get( 'num_turns', '?' )} turn(s), { secs }s" )
            if final.get( "is_error" ) or how != "success":
                return self._fail( f"claude stopped with { how }: { _clip( answer ) or 'no answer' }", exit_code=code,
                                   turns=final.get( "num_turns" ), subtype=how, seconds=secs,
                                   error_source="claude_cli_turn_limit" if how == "error_max_turns" else "claude_cli" )
            if answer:
                return TaskResult( ok=True, text=answer, performer=self.performer,
                                   details={ "exit_code": code, "turns": final.get( "num_turns" ), "seconds": secs, "subtype": how })
        if code not in ( 0, None ):
            tail = stderr.strip().splitlines()[ -3: ]
            return self._fail( f"claude exited { code }: { ' | '.join( tail ) or 'no error text' }", exit_code=code )
        return self._fail( "claude finished but gave no answer", exit_code=code )

    def _emit( self, envelope: Envelope, phase: str, message: str ) -> None:
        if self._progress is None:
            return
        if self._leaks( message ):
            message = "(step withheld: it looked like it contained a credential)"
        try:
            self._progress( envelope, phase, message )
        except Exception:                      # noqa: BLE001 -- progress is best effort; the work is what matters
            pass

    def _prompt( self, envelope: Envelope ) -> str:
        return ( WRITE_RULES if self._writes else READ_RULES ) + envelope.text

    def _leaks( self, text: str ) -> bool:
        if not text:
            return False
        if JWT.search( text ):
            return True
        return any( value in text for value in self._secret_values())

    def _secret_values( self ) -> set[ str ]:
        """Every long string in the login file, read fresh: the login rotates."""
        values: set[ str ] = set()
        for path in self._secrets:
            try:
                stack = [ json.loads( path.read_text( encoding="utf-8" ))]
            except ( OSError, ValueError ):
                continue
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
        return self._jail.wrap( argv, workdir if self._writes else root, workdir, [ scratch ])

    def _end_group( self, process: subprocess.Popen ) -> None:
        for sig, wait in (( signal.SIGTERM, self._grace ), ( signal.SIGKILL, None )):
            try:
                os.killpg( process.pid, sig )
            except ProcessLookupError:
                break
            try:
                process.wait( timeout=wait )
                break
            except subprocess.TimeoutExpired:
                continue
