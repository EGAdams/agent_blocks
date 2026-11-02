"""PrinterAgent: the IAgentAdapter for the four printer operations. No model in the path.

The gateway already checked scope, `confirm: true`, idempotency and rate. This
adapter only turns one claimed task into one run of `adws/adw_printer.py`
(through `IPrinterCommands`) and its one JSON answer into an AgentOutcome:

    print           device_control   the prompt is a PrintRequest (JSON), passed on stdin
    print_cancel    device_control   `cancel=<job id>`
    print_status    read_only        `job=<job id>` or `recent`
    print_discover  read_only        `discover`

The job id of a print is the bridge task id, so a retried task finds the first
attempt in the printer's registry and prints nothing. Nothing from the prompt
reaches an argv: the operation picks the subcommand, the job id is checked
against the task-id pattern, and a print request travels on stdin.

Once a print has started it runs to its end even if the client cancels or the
lease is lost -- killing it mid-hand-off would leave a job that may or may not
have printed. A cancel that arrives in time is `printer_cancel`'s job.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER  = "printer"
JOB_ID     = r"t_[0-9a-f]{24}"
CANCEL     = re.compile( rf"cancel=({ JOB_ID })" )
STATUS     = re.compile( rf"job=({ JOB_ID })|recent" )
REACHED    = frozenset({ "submitted_to_spooler", "completed_per_spooler" })
SUBCOMMAND = { "print": "print", "print_cancel": "cancel", "print_status": "status", "print_discover": "discover" }


class PrinterCommandFailed( Exception ):
    """The printer ADW did not give an answer (it crashed, timed out, or printed no JSON)."""


class IRunningCommand( Protocol ):
    def poll( self ) -> dict | None: ...           # None while running; the ADW's JSON answer once done

    def stop( self ) -> None: ...                  # only for commands that never print


class IPrinterCommands( Protocol ):
    def start( self, subcommand: str, job_id: str | None, request: str | None ) -> IRunningCommand: ...


class PrinterAgent:
    def __init__( self, commands: IPrinterCommands, clock: IClock, poll_seconds: float = 1.0,
                  deadline_seconds: float = 900.0 ) -> None:
        self._commands = commands
        self._clock    = clock
        self._poll     = poll_seconds
        self._deadline = deadline_seconds

    # -- IAgentAdapter ------------------------------------------------------------

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        subcommand = SUBCOMMAND.get( task.operation )
        if subcommand is None:
            return self._refused( f"the printer does not offer { task.operation }" )
        job_id, request = task.task_id, None
        prompt = task.prompt.strip()
        if task.operation == "print":
            request = task.prompt
        elif task.operation == "print_cancel":
            found = CANCEL.fullmatch( prompt )
            if found is None:
                return self._refused( "a cancel task says cancel=<job id> and nothing else" )
            job_id = found.group( 1 )
        elif task.operation == "print_status":
            found = STATUS.fullmatch( prompt )
            if found is None:
                return self._refused( "a status task says job=<job id> or recent, and nothing else" )
            job_id, subcommand = ( found.group( 1 ), "status" ) if found.group( 1 ) else ( None, "recent" )
        else:
            if prompt != "discover":
                return self._refused( "a discover task says discover and nothing else" )
            job_id = None
        try:
            answer, signal = self._wait( self._commands.start( subcommand, job_id, request ), task, keep_alive )
        except PrinterCommandFailed as exc:
            return self._failed( task, f"the printer workflow gave no answer ({ exc })" )
        if signal == "lost":
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        return self._outcome( task, subcommand, answer, cancelled_late=signal == "cancel" )

    # -- waiting ------------------------------------------------------------------------

    def _wait( self, running: IRunningCommand, task: ClaimedTask, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A print is never stopped; a read-only command may be."""
        prints   = task.operation in ( "print", "print_cancel" )
        deadline = self._clock.monotonic() + self._deadline
        seen: Signal = "continue"
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, seen
            if self._clock.monotonic() >= deadline:
                if not prints:
                    running.stop()
                    raise PrinterCommandFailed( "timed out" )
                # a print past its deadline keeps running; the registry will say how it ended
            signal = keep_alive()
            if signal != "continue":
                seen = signal
                if not prints:
                    running.stop()
                    return {}, signal
            self._clock.sleep( self._poll )

    # -- outcomes -----------------------------------------------------------------------

    def _outcome( self, task: ClaimedTask, subcommand: str, answer: dict, cancelled_late: bool ) -> AgentOutcome:
        if cancelled_late and subcommand not in ( "print", "cancel" ):
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        if not isinstance( answer, dict ) or "error" in answer:
            return self._failed( task, str(( answer or {}).get( "error" ) or "the printer workflow answered nothing" ))
        if subcommand == "discover":
            names   = ", ".join( f"{ p.get( 'printer_id' )}{ ' (default)' if p.get( 'is_default' ) else '' }"
                                 for p in answer.get( "printers", []))
            details = { "printer": "discover", **answer }
            return self._done( f"{ len( answer.get( 'printers', []))} printer(s): { names or 'none' }",
                               "Printers on this machine, the default, the printable documents and recent jobs are in details.",
                               details )
        if subcommand == "recent":
            jobs = answer.get( "recent", answer.get( "jobs", []))   # adw_printer.py says {"recent": [...]}
            return self._done( f"{ len( jobs )} recent print job(s)", "The recent jobs are in details.",
                               { "printer": "recent", "jobs": jobs })
        # adw_printer.py wraps a print, status or cancel answer as {"job": {...}}.
        job   = answer[ "job" ] if isinstance( answer.get( "job" ), dict ) else answer
        state = str( job.get( "state", "" ))
        line  = describe( job )
        if cancelled_late:
            line += " (the cancel arrived after the job was already running, so it ran to its end; use printer_cancel)"
        details = { "printer": subcommand, "job": job }
        if subcommand == "status":
            return self._done( line, line, details )
        if subcommand == "cancel":
            ok = state == "canceled"
            return self._done( line, line, details ) if ok else self._failed( task, line, details )
        if state in REACHED:
            return self._done( line, line, details )
        return self._failed( task, line, details )

    @staticmethod
    def _done( summary: str, body: str, details: dict ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=summary[ :300 ], body=body,
                             details=details )

    @staticmethod
    def _failed( task: ClaimedTask, summary: str, details: dict | None = None ) -> AgentOutcome:
        # Never retryable: a retry of a print could print twice.
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ],
                             body=summary, retryable=False, details=details )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )


def describe( job: dict ) -> str:
    """One sentence for ChatGPT. Never claims paper came out: the spooler cannot say that."""
    state   = str( job.get( "state", "unknown" ))
    title   = str( job.get( "title" ) or "untitled" )[ :80 ]
    where   = job.get( "printer" ) or job.get( "printer_id" ) or "no printer"
    pages   = job.get( "pages" )
    copies  = job.get( "copies" ) or 1
    size    = f"{ pages } page(s) x { copies }" if pages else f"{ copies } cop{ 'y' if copies == 1 else 'ies' }"
    detail  = str( job.get( "detail" ) or "" )[ :200 ]
    said = {
        "submitted_to_spooler":  f"handed to the Windows print queue for { where }; it has not left the queue yet",
        "completed_per_spooler": f"the Windows print queue for { where } reports it finished; paper output is not verified",
        "canceled":              "canceled",
        "failed":                "not printed",
        "ambiguous":             "may or may not have printed and will not be reprinted automatically -- check the printer",
    }.get( state, state )
    return f"Print job { job.get( 'job_id' )} '{ title }' ({ size }): { said }." + ( f" { detail }" if detail else "" )
