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
from pathlib import Path

from ..models import Envelope, JailUnavailable, TaskResult
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

Task:
"""


class CodexCliExecutor:

    def __init__( self, workdir: Path, binary: str = "codex", timeout_seconds: float = 900.0,
                  grace_seconds: float = 10.0, model: str | None = None,
                  jail: ICommandJail | None = None, secret_files: list[ Path ] | None = None,
                  sandbox: str = "read-only", read_roots: list[ Path ] | None = None,
                  write_roots: list[ Path ] | None = None ) -> None:
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

    @property
    def performer( self ) -> str:
        return f"codex:{ self._sandbox }"

    def argv( self, workdir: Path, outfile: Path ) -> list[ str ]:
        model = [ "-m", self._model ] if self._model else []
        return [ self._binary, "exec", "--sandbox", self._sandbox, *model, "--cd", str( workdir ),
                 "--ephemeral", "--skip-git-repo-check", "--color", "never", "-o", str( outfile ), "-" ]

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
            try:
                _, stderr = process.communicate( self._prompt( envelope ), timeout=self._timeout )
            except subprocess.TimeoutExpired:
                self._end_group( process )
                return TaskResult( ok=False, text=f"codex did not finish within { round( self._timeout ) }s",
                                   performer=self.performer, details={ "exit_code": None, "timed_out": True })
            answer = outfile.read_text( encoding="utf-8" ).strip() if outfile.exists() else ""
        if self._leaks( answer ) or self._leaks( stderr or "" ):
            return TaskResult( ok=False, text=WITHHELD, performer=self.performer,
                               details={ "exit_code": process.returncode, "withheld": True })
        if process.returncode != 0:
            tail = ( stderr or "" ).strip().splitlines()[ -3: ]
            return TaskResult( ok=False, text=f"codex exited { process.returncode }: { ' | '.join( tail ) or 'no error text' }",
                               performer=self.performer, details={ "exit_code": process.returncode })
        if not answer:
            return TaskResult( ok=False, text="codex finished but wrote no answer", performer=self.performer,
                               details={ "exit_code": 0 })
        return TaskResult( ok=True, text=answer, performer=self.performer, details={ "exit_code": 0 })

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

    def _end_group( self, process: subprocess.Popen ) -> None:
        for sig, wait in (( signal.SIGTERM, self._grace ), ( signal.SIGKILL, None )):
            try:
                os.killpg( process.pid, sig )
            except ProcessLookupError:
                break
            try:
                process.communicate( timeout=wait )
                break
            except subprocess.TimeoutExpired:
                continue
