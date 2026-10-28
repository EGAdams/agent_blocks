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
    """One ADW script as an executor: the request text is its first argument."""

    VERDICTS = ( "DONE:", "NO_CHANGE:", "DIAGNOSIS:", "REFUSED:", "BUSY:" )
    OK       = ( "DONE:", "NO_CHANGE:", "DIAGNOSIS:" )

    def __init__( self, script: Path, cwd: Path, extra_args: list[ str ] | None = None,
                  python: str | None = None, timeout_seconds: float = 3600.0 ) -> None:
        self._script  = Path( script )
        self._cwd     = Path( cwd )
        self._extra   = list( extra_args or [])
        self._python  = python or sys.executable
        self._timeout = timeout_seconds

    @property
    def performer( self ) -> str:
        return f"adw:{ self._script.name }"

    async def execute( self, envelope: Envelope ) -> TaskResult:
        return await asyncio.to_thread( self._run, envelope )

    def _run( self, envelope: Envelope ) -> TaskResult:
        argv = [ self._python, str( self._script ), envelope.text, *self._extra ]
        if envelope.context.get( "diagnose_only" ):
            argv.append( "--diagnose" )
        try:
            done = subprocess.run( argv, cwd=self._cwd, capture_output=True, text=True, timeout=self._timeout )
        except subprocess.TimeoutExpired:
            return TaskResult( ok=False, performer=self.performer,
                               text=f"{ self._script.name } did not finish within { int( self._timeout ) } seconds" )
        lines   = ( done.stdout or "" ).strip().splitlines()
        verdict = next(( l for l in reversed( lines ) if l.startswith( self.VERDICTS )), "" )
        if verdict:
            return TaskResult( ok=verdict.startswith( self.OK ), text=verdict, performer=self.performer,
                               details={ "exit_code": done.returncode })
        tail = ( done.stderr or done.stdout or "" ).strip().splitlines()
        return TaskResult( ok=done.returncode == 0, performer=self.performer,
                           text=tail[ -1 ] if tail else f"{ self._script.name } exited { done.returncode }",
                           details={ "exit_code": done.returncode })
