"""PiCodingAgentExecutor -- ITaskExecutor over the Pi Coding Agent CLI (`pi -p --mode json`), jailed like Claude.

Sheila is the Pi agent on BATI. She is not the Claude Agent SDK and not an alias for Claude Code: this
executor never starts anything but the configured Pi binary. It follows `claude_cli.py` where the two
problems are the same, and differs where Pi does:

  * the model is **pinned** (`provider/id`, `openai-codex/gpt-6-sol` on her card) and confirmed per task from
    the `provider` and `model` Pi stamps on every assistant message; a task that was served by anything else
    fails as `model_mismatch`, it is never quietly accepted. Pi's own default provider is not signed in on
    this machine, so a missing `--model` would be a failure, not a different model;
  * Pi has no sandbox of its own, so every run is inside BwrapJail (a writer refuses to build without one):
    `/usr` and `/etc` read-only, the one workspace (writable only for a writer, `.git` read-only), private
    paths masked, environment cleared, an empty `$HOME`, Pi's own state in a dedicated directory
    (`PI_CODING_AGENT_DIR`, ~/.pi-bridge/agent -- none of EG's own Pi sessions);
  * the jail's network is open (Pi must reach its provider), so what it sees of the login is a copy with the
    *refresh* token removed and the other provider's login left out (`PiLoginLifetime.write_jail_copy`), good
    only until the access token expires, which `run_limit()` already caps every run to. The real login is
    never mounted;
  * the tools are fixed when the executor is built, never by the request: read-only is `read,grep,find,ls`; a
    writer adds `edit,write`; `bash` only when the card says `shell: true`. Extensions, skills, context files
    and prompt templates are switched off, so nothing in the workspace can add a tool or change the persona;
  * stdin is closed (`DEVNULL`): Pi waits on an open stdin for input that never comes.

Pi's `--mode json` stream is read as it arrives. Every tool call it really makes is reported through
`progress` (tool name and the file or pattern only); the last assistant message's text is the answer. Answer
or error text that carries a login value or looks like a JWT is withheld. A timeout ends the whole process
group, TERM then KILL.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..models import Envelope, JailUnavailable, TaskResult
from ..ports import ICommandJail
from .claude_cli import CLIP, JWT, LOGIN_MARGIN, MAX_PROGRESS, SHORTEST_RUN, ILoginLifetime, LoginStale, Progress

READ_TOOLS  = ( "read", "grep", "find", "ls" )
WRITE_TOOLS = ( *READ_TOOLS, "edit", "write" )
SHELL_TOOLS = ( *WRITE_TOOLS, "bash" )
STRIPPED    = ( "refresh", )                      # never shown to anything inside the jail
WITHHELD    = "Sheila's answer was withheld: it contained login credentials"

PERSONA = """You are Sheila, a lightweight autonomous coding and operations agent running on BATI, reached through \
EG's agent post office. Follow the GoF manual and OMAPS. Inspect before replacing; least privilege; small testable \
changes; verify results and report errors and timeouts plainly -- never claim success you did not check. Send no \
email yourself: route any email request to yolanda."""

READ_RULES = """You are answering read-only. Use the read, grep, find and ls tools on the current directory; do not \
try to change anything. Answer plainly and name the files you relied on.

Task:
"""

WRITE_RULES = """You are working in a dedicated workspace on EG's machine. You may create and edit files in the \
current directory only, with the edit and write tools. You cannot run commands; the worker runs the workspace's \
checks after you finish. Do not delete files you did not create in this task. Finish with a short summary of the \
files you created or changed.

Task:
"""

SHELL_RULES = """You are working in a dedicated workspace on EG's machine. You may create and edit files in the \
current directory with the edit and write tools, and run commands there with the bash tool (tests, scripts, \
builds, git log/diff/status). Git cannot commit here and everything outside this directory is hidden: do not try \
to get around that. Never read, print or send any login or credential file, and do not delete files you did not \
create in this task. Finish with a short summary of the files you created or changed and the commands you ran \
with their results.

Task:
"""

TOOL_VERBS = { "read": "read", "grep": "searched", "find": "listed files in", "ls": "listed", "edit": "edited",
               "write": "wrote", "bash": "ran" }


class PiLoginLifetime:
    """ILoginLifetime over Pi's own `auth.json`. Reads one provider's expiry only; never logs a value."""

    def __init__( self, auth_file: Path, provider: str, refresh_command: list[ str ], env: dict[ str, str ] | None = None,
                  timeout_seconds: float = 120.0 ) -> None:
        self._file     = Path( auth_file )
        self._provider = provider
        self._command  = list( refresh_command )
        self._env      = dict( env if env is not None else os.environ )
        self._timeout  = timeout_seconds

    def seconds_left( self ) -> float:
        try:
            entry = json.loads( self._file.read_text( encoding="utf-8" )).get( self._provider ) or {}
            return float( entry.get( "expires", 0 )) / 1000.0 - time.time()
        except ( OSError, ValueError, TypeError, AttributeError ):
            return 0.0

    def write_jail_copy( self, destination: Path ) -> None:
        """This provider's login minus its refresh token, 0600, swapped in whole: all the jail may see."""
        entry = dict( json.loads( self._file.read_text( encoding="utf-8" )).get( self._provider ) or {})
        if not entry:
            raise ValueError( f"no { self._provider } login in { self._file.name }" )
        for key in STRIPPED:
            entry.pop( key, None )
        destination = Path( destination )
        destination.parent.mkdir( mode=0o700, parents=True, exist_ok=True )
        temporary = destination.with_name( destination.name + f".{ os.getpid() }.tmp" )
        descriptor = os.open( temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 )
        with os.fdopen( descriptor, "w", encoding="utf-8" ) as handle:
            json.dump({ self._provider: entry }, handle )
        os.replace( temporary, destination )

    def refresh( self ) -> None:
        """One tool-less, unsaved Pi turn outside the jail: Pi renews its own login as it starts."""
        try:
            subprocess.run( self._command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=self._timeout,
                            env=self._env, start_new_session=True )
        except ( OSError, subprocess.TimeoutExpired ):
            pass                        # judged by seconds_left() afterwards, not by this exit


def refresh_command( binary: str, model: str ) -> list[ str ]:
    return [ binary, "-p", "--no-tools", "--no-session", "--no-extensions", "--no-skills", "--no-context-files",
             "--no-prompt-templates", "--model", model, "Reply with OK." ]


def _clip( text: str ) -> str:
    text = " ".join( str( text ).split())
    return text if len( text ) <= CLIP else text[ : CLIP - 1 ] + "..."


def _target( args: dict[ str, Any ], root: Path ) -> str:
    """The file or pattern a tool call names, relative to the workspace when it is inside it; never a command's output."""
    value = args.get( "path" ) or args.get( "file_path" ) or args.get( "pattern" ) or args.get( "command" ) or ""
    text  = str( value )
    try:
        text = str( Path( text ).resolve().relative_to( root.resolve()))
    except ( ValueError, OSError ):
        pass
    return _clip( text )


def progress_message( event: dict[ str, Any ], root: Path ) -> list[ tuple[ str, str ]]:
    """What Pi really did, as ( phase, message ): tool calls only, never the prompt or the reply."""
    if event.get( "type" ) != "tool_execution_start":
        return []
    name = str( event.get( "toolName" ) or "" )
    args = event.get( "args" ) if isinstance( event.get( "args" ), dict ) else {}
    verb = TOOL_VERBS.get( name, name )
    return [( "tool", f"{ verb } { _target( args, root ) }".strip())]


class PiCodingAgentExecutor:
    """ITaskExecutor Adapter over the Pi Coding Agent CLI."""

    def __init__( self, workdir: Path, binary: str = "pi", model: str = "openai-codex/gpt-6-sol",
                  timeout_seconds: float = 900.0, grace_seconds: float = 10.0, writes: bool = False,
                  shell: bool = False, jail: ICommandJail | None = None, login: ILoginLifetime | None = None,
                  jail_login: Path | None = None, secret_files: list[ Path ] | None = None,
                  read_roots: list[ Path ] | None = None, write_roots: list[ Path ] | None = None,
                  progress: Progress | None = None ) -> None:
        if shell and not writes:
            raise ValueError( "a shell is for a write-enabled Sheila only; a reader never gets bash" )
        if jail is None:
            raise ValueError( "a Sheila executor needs a jail: Pi has no sandbox of its own and never runs unconfined" )
        if read_roots and writes:
            raise ValueError( "read_roots are for a read-only Sheila; a writer names write_roots" )
        if write_roots and not writes:
            raise ValueError( "write_roots are for a write-enabled Sheila only" )
        if not re.fullmatch( r"[A-Za-z0-9._-]{1,64}/[A-Za-z0-9._:-]{1,96}", model ):
            raise ValueError( "the Pi model must be pinned as provider/id" )
        if login is None:
            raise ValueError( "a Sheila executor needs an ILoginLifetime" )
        self._workdir  = Path( workdir )
        self._roots    = [ Path( workdir ), *[ Path( r ) for r in [ *( read_roots or []), *( write_roots or [])]]]
        self._binary   = binary
        self._model    = model
        self._timeout  = timeout_seconds
        self._grace    = grace_seconds
        self._writes   = writes
        self._shell    = shell
        self._jail     = jail
        self._login    = login
        self._jail_login = Path( jail_login ) if jail_login is not None else None
        self._secrets  = [ Path( f ) for f in secret_files or []]
        self._progress = progress

    @property
    def performer( self ) -> str:
        if self._writes:
            return "pi:workspace-write+shell" if self._shell else "pi:workspace-write"
        return "pi:read-only"

    def available( self ) -> bool:
        return os.access( self._binary, os.X_OK )

    def argv( self, prompt: str ) -> list[ str ]:
        tools = ",".join( SHELL_TOOLS if self._shell else WRITE_TOOLS if self._writes else READ_TOOLS )
        return [ self._binary, "-p", "--mode", "json", "--no-session", "--no-extensions", "--no-skills",
                 "--no-context-files", "--no-prompt-templates", "--offline", "--tools", tools,
                 "--model", self._model, "--append-system-prompt", PERSONA, prompt ]

    def run_limit( self ) -> float:
        """Seconds this run may take: the card's limit, cut to what the login has left."""
        left = self._login.seconds_left()
        if left < self._timeout + LOGIN_MARGIN:
            self._login.refresh()
            left = self._login.seconds_left()
        limit = min( self._timeout, left - LOGIN_MARGIN )
        if limit < min( SHORTEST_RUN, self._timeout ):
            raise LoginStale( "Pi's login has expired or is about to, and renewing it failed; sign in again with `pi` "
                              "on this machine (/login)" )
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
        if not self.available():
            return self._fail( f"Pi Coding Agent ('{ self._binary }') is not installed on this machine", available=False )
        try:
            root, workdir = self._workdir_for( envelope )
        except PermissionError as error:
            return self._fail( str( error ))
        try:
            limit = self.run_limit()
        except LoginStale as error:
            return self._fail( str( error ))
        if self._jail_login is not None:
            try:
                self._login.write_jail_copy( self._jail_login )
            except ( OSError, ValueError ) as error:
                return self._fail( f"Sheila not started, the login copy for her jail could not be made: { error }" )
        try:
            command = self._jailed( self.argv( self._prompt( envelope )), root, workdir )
        except JailUnavailable as error:
            return self._fail( f"Sheila not started, her jail is unavailable: { error }" )
        try:
            process = subprocess.Popen( command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, errors="replace", cwd=str( workdir ), start_new_session=True )
        except OSError as error:
            return self._fail( f"could not start Pi: { error }" )
        self._emit( envelope, "started", f"Pi ({ self._model }) started on { envelope.task_type } in { workdir.name } "
                                         f"({ self.performer.split( ':' )[ 1 ]}); pid={ process.pid }, timeout={ limit }s" )
        stderr_lines: list[ str ] = []
        drain = threading.Thread( target=lambda: stderr_lines.extend( process.stderr ), daemon=True )
        drain.start()
        messages, timed_out = self._stream( envelope, process, workdir, limit )
        drain.join( timeout=5 )
        stderr = "".join( stderr_lines )
        result = self._confirm_model( self._result( envelope, process.returncode, messages, timed_out, stderr, limit ), messages )
        return result.model_copy( update={ "details": {
            **result.details, "model": self._model, "timeout_seconds": limit, "child_pid": process.pid,
            "process_group_id": process.pid, "bridge_task_id": envelope.context.get( "bridge_task_id" ),
            "local_task_id": envelope.id,
        }})

    def _stream( self, envelope: Envelope, process: subprocess.Popen, workdir: Path,
                 limit: float ) -> tuple[ list[ dict[ str, Any ]], bool ]:
        expired = threading.Event()

        def stop() -> None:
            expired.set()
            self._end_group( process )

        timer = threading.Timer( limit, stop )
        timer.daemon = True
        timer.start()
        assistant: list[ dict[ str, Any ]] = []
        sent = hidden = 0
        try:
            for line in process.stdout:
                try:
                    event = json.loads( line )
                except ValueError:
                    continue
                if not isinstance( event, dict ):
                    continue
                message = event.get( "message" )
                if event.get( "type" ) == "message_end" and isinstance( message, dict ) and message.get( "role" ) == "assistant":
                    assistant.append( message )
                for phase, text in progress_message( event, workdir ):
                    if sent < MAX_PROGRESS:
                        self._emit( envelope, phase, text )
                        sent += 1
                    else:
                        hidden += 1
            process.wait()
        finally:
            timer.cancel()
        if hidden:
            self._emit( envelope, "tool", f"{ hidden } further step(s) not listed" )
        return assistant, expired.is_set()

    def _confirm_model( self, result: TaskResult, messages: list[ dict[ str, Any ]]) -> TaskResult:
        """Record which model really served the turn; any other is a failure, never a quiet swap."""
        used  = sorted({ f"{ m.get( 'provider' ) }/{ m.get( 'model' ) }" for m in messages if m.get( "model" )})
        facts = { "model_requested": self._model, "models_used": ",".join( used ) }
        if used and used != [ self._model ]:
            return TaskResult( ok=False, performer=result.performer, details={ **result.details, **facts, "model_confirmed": False,
                                                                                 "error_source": "model_mismatch" },
                               text=f"model_mismatch: asked for { self._model } but Pi reports { ', '.join( used ) }; "
                                    "the answer was not accepted" )
        return result.model_copy( update={ "details": { **result.details, **facts, "model_confirmed": bool( used ) or None }})

    def _result( self, envelope: Envelope, code: int | None, messages: list[ dict[ str, Any ]], timed_out: bool,
                 stderr: str, limit: float ) -> TaskResult:
        last   = messages[ -1 ] if messages else {}
        answer = "".join( p.get( "text", "" ) for p in last.get( "content" ) or []
                          if isinstance( p, dict ) and p.get( "type" ) == "text" ).strip()
        if self._leaks( answer ) or self._leaks( stderr ):
            self._emit( envelope, "finished", "Pi finished; its answer was withheld (credential-shaped text)" )
            return self._fail( WITHHELD, exit_code=code, withheld=True )
        if timed_out:
            self._emit( envelope, "finished", f"stopped after { round( limit )}s without finishing" )
            return self._fail( f"Pi did not finish within { round( limit )}s", exit_code=None, timed_out=True )
        stop = last.get( "stopReason" )
        self._emit( envelope, "finished", f"Pi finished: { stop or 'no answer' }, { len( messages )} assistant message(s)" )
        if stop in ( "error", "aborted" ):
            return self._fail( f"Pi stopped with { stop }: { _clip( last.get( 'errorMessage' ) or answer ) or 'no error text'}",
                               exit_code=code, stop_reason=stop, error_source="pi_cli" )
        if code not in ( 0, None ):
            tail = stderr.strip().splitlines()[ -3: ]
            return self._fail( f"Pi exited { code }: { ' | '.join( tail ) or 'no error text' }", exit_code=code )
        if answer:
            return TaskResult( ok=True, text=answer, performer=self.performer,
                               details={ "exit_code": code, "assistant_messages": len( messages ), "stop_reason": stop })
        return self._fail( "Pi finished but gave no answer", exit_code=code )

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
        rules = SHELL_RULES if self._shell else WRITE_RULES if self._writes else READ_RULES
        return rules + envelope.text

    def _leaks( self, text: str ) -> bool:
        if not text:
            return False
        if JWT.search( text ):
            return True
        return any( value in text for value in self._secret_values())

    def _secret_values( self ) -> set[ str ]:
        """Every long string in the login files, read fresh: a login rotates."""
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

    def _jailed( self, argv: list[ str ], root: Path, workdir: Path ) -> list[ str ]:
        return self._jail.wrap( argv, workdir if self._writes else root, workdir, [])

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
