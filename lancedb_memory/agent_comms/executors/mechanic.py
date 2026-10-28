"""The mechanic's executors.

* `DiagnosisExecutor` -- `diagnose` work. Code gathers the facts
  (IAgentDiagnostic), the case is recorded (ICaseHistory), and the reply is
  the factual summary. An optional `explainer` -- any ITaskExecutor, e.g.
  Opus through ClaudeSdkExecutor -- may then read the facts and propose a fix;
  swapping it in or out changes nothing else.
* `AdwSubprocessExecutor` -- runs one ADW script and reports its last
  verdict line. The mechanic's `repair` work is `adws/adw_toyota_mechanic.py`,
  which edits only a sandbox copy and applies only if every suite stays green
  -- the existing, guarded path; nothing here bypasses it.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

from ..models import Envelope, TaskResult
from ..ports import IAgentDiagnostic, ICaseHistory, ITaskExecutor


class DiagnosisExecutor:

    def __init__( self, diagnostic: IAgentDiagnostic, cases: ICaseHistory,
                  explainer: ITaskExecutor | None = None ) -> None:
        self._diagnostic = diagnostic
        self._cases      = cases
        self._explainer  = explainer

    @property
    def performer( self ) -> str:
        tail = f"+{ self._explainer.performer }" if self._explainer else ""
        return f"python:diagnosis{ tail }"

    async def execute( self, envelope: Envelope ) -> TaskResult:
        task_id = str( envelope.context.get( "task_id" ) or "" )
        if not task_id:
            return TaskResult( ok=False, performer=self.performer,
                               text="A diagnose request needs context.task_id: which task failed?" )
        diagnosis = self._diagnostic.diagnose( task_id, str( envelope.context.get( "adw_id" ) or "" ) or None )
        if not diagnosis.found:
            return TaskResult( ok=False, performer=self.performer, text=diagnosis.summary())
        case_id = self._cases.record( diagnosis )
        text    = f"{ diagnosis.summary() } Recorded as { case_id }."
        details = { "case_id": case_id, "diagnosed_task": task_id,
                    "failed_phase": diagnosis.failed_phase or "", "adw_id": diagnosis.adw_id or "" }
        if self._explainer is not None:
            advice = await self._explainer.execute( envelope.model_copy( update={
                "task_type": "ask",
                "text": "Diagnose this failure and propose the smallest fix:\n" + diagnosis.model_dump_json( indent=2 ),
            }))
            text = f"{ text } { advice.text }" if advice.ok else text
        return TaskResult( ok=True, text=text, performer=self.performer,
                           details={ **details, "diagnosis": diagnosis.model_dump( mode="json" )})


class AdwSubprocessExecutor:
    """One ADW script as an executor: the request text is its first argument.

    `command` is how the script is run -- the current interpreter by default,
    a venv's python, or `[ "uv", "run", "--quiet" ]` for a PEP 723 script that
    declares its own dependencies, as the browser-workflow ADWs do.
    `diagnose_flag` is appended for `context.diagnose_only` (only the
    mechanic's ADW has one); `sender_flag`, when set, passes who asked.

    The script runs in its own process group, and a timeout ends the whole
    group: behind `uv run` the script is a grandchild, and killing only `uv`
    would leave it driving the user's browser after the task was failed."""

    VERDICTS = ( "DONE:", "NO_CHANGE:", "DIAGNOSIS:", "REFUSED:", "BUSY:" )
    OK       = ( "DONE:", "NO_CHANGE:", "DIAGNOSIS:" )
    GRACE_SECONDS = 10.0

    def __init__( self, script: Path, cwd: Path, extra_args: list[ str ] | None = None,
                  command: list[ str ] | None = None, timeout_seconds: float = 3600.0,
                  diagnose_flag: str | None = "--diagnose", sender_flag: str | None = None ) -> None:
        self._script   = Path( script )
        self._cwd      = Path( cwd )
        self._extra    = list( extra_args or [])
        self._command  = list( command or [ sys.executable ])
        self._timeout  = timeout_seconds
        self._diagnose = diagnose_flag
        self._sender   = sender_flag

    @property
    def performer( self ) -> str:
        return f"adw:{ self._script.name }"

    async def execute( self, envelope: Envelope ) -> TaskResult:
        return await asyncio.to_thread( self._run, envelope )

    def _argv( self, envelope: Envelope ) -> list[ str ]:
        argv = [ *self._command, str( self._script ), envelope.text, *self._extra ]
        if self._sender:
            argv += [ self._sender, envelope.sender ]
        if self._diagnose and envelope.context.get( "diagnose_only" ):
            argv.append( self._diagnose )
        return argv

    def _run( self, envelope: Envelope ) -> TaskResult:
        process = subprocess.Popen( self._argv( envelope ), cwd=self._cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, start_new_session=True )
        try:
            stdout, stderr = process.communicate( timeout=self._timeout )
        except subprocess.TimeoutExpired:
            self._end_group( process )
            return TaskResult( ok=False, performer=self.performer,
                               text=f"{ self._script.name } did not finish within { int( self._timeout ) } seconds" )
        lines   = ( stdout or "" ).strip().splitlines()
        verdict = next(( l for l in reversed( lines ) if l.startswith( self.VERDICTS )), "" )
        if verdict:
            return TaskResult( ok=verdict.startswith( self.OK ), text=verdict, performer=self.performer,
                               details={ "exit_code": process.returncode })
        tail = ( stderr or stdout or "" ).strip().splitlines()
        return TaskResult( ok=process.returncode == 0, performer=self.performer,
                           text=tail[ -1 ] if tail else f"{ self._script.name } exited { process.returncode }",
                           details={ "exit_code": process.returncode })

    def _end_group( self, process: subprocess.Popen ) -> None:
        try:
            os.killpg( process.pid, signal.SIGTERM )
        except ProcessLookupError:
            pass
        try:
            process.communicate( timeout=self.GRACE_SECONDS )
        except subprocess.TimeoutExpired:
            pass
        finally:
            # communicate() can finish when the parent exits even while a
            # grandchild with redirected output ignores TERM. End that group
            # too, then reap the direct child and drain its pipes.
            try:
                os.killpg( process.pid, signal.SIGKILL )
            except ProcessLookupError:
                pass
            process.communicate()
