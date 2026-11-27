"""MachineSupervisorAgent: the IAgentAdapter that lets the Star Trek Bridge read the Machine Supervisors'
mailboxes on BATI, SQMC and R46, and give one of them a todo. No model in this path.

The prompt is JSON written by code (`submit_agent_task` to agent and workspace `machine-supervisor`):

    {"supervisor": "machines"}
    {"supervisor": "status", "machine": "SQMC"}
    {"supervisor": "mailbox", "machine": "BATI", "box": "new"}
    {"supervisor": "read", "machine": "BATI", "id": "20261114T120001Z_6eee5cde_from_supervisor-sqmc"}
    {"supervisor": "todo", "machine": "R46", "text": "Find out why the scanner service restarted last night."}

    inspect    read_only        machines | status | mailbox | read
    implement  workspace_write  todo                       (queued in that supervisor's inbox; she works it later)

A todo's task succeeds when the todo is *queued*, not done, so its result carries `details.handoff`: the read
that proves completion. The gateway keeps a request's deliverable "submitted" -- not met -- until a later
`{"supervisor": "read", ...}` task finds the todo in completed (met) or failed (failed), the same contract a
print job has with its spooler check. Following it here instead would hold a worker lane for the todo's whole
run (up to an hour), and the express lane exists so that diagnostics never wait behind long work.

Each task is one run of `adws/adw_machine_mailbox.py` or `adws/adw_machine_todo.py`. A request names a machine,
a box and a message id -- never a path, never a command -- and each value is checked here before it becomes a
`--flag=value`. A todo's text is free text for the supervisor on that machine to read; it is one argv item, it
is never run by a shell, and she works it under the same deny-table guard as a repair. The Bridge sender is
passed as `--client`, and the receiving machine decides whether that sender may give todos at all.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .deploy_agent import DeployCommandFailed, IRunningDeployCommand
from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER = "machine_supervisor_adw"
READS     = frozenset({ "machines", "status", "mailbox", "read" })
WRITES    = frozenset({ "todo" })
TEXT_MAX  = 4000
#: request key -> ADW flag, and the pattern its value must match. `text` is checked apart: it is prose.
FLAGS     = { "machine": ( "machine", r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}" ),
              "box":     ( "box", r"(all|new|in_process|completed|failed|outbox|sent|undelivered)" ),
              "id":      ( "id", r"[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}_from_[a-z][a-z0-9-]{0,60}" )}
NEEDS     = { "machines": (), "status": ( "machine", ), "mailbox": ( "machine", ), "read": ( "machine", "id" ),
              "todo": ( "machine", "text" )}
ALLOWS    = { "machines": (), "status": ( "machine", ), "mailbox": ( "machine", "box" ), "read": ( "machine", "id" ),
              "todo": ( "machine", "text" )}


class IMachineSupervisorCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ]) -> IRunningDeployCommand: ...


class MachineSupervisorAgent:
    def __init__( self, commands: IMachineSupervisorCommands, clock: IClock, poll_seconds: float = 1.0,
                  deadline_seconds: float = 180.0 ) -> None:
        self._commands, self._clock, self._poll, self._deadline = commands, clock, poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "supervisor" ), str ):
            return self._refused( 'a supervisor task is JSON, e.g. {"supervisor": "status", "machine": "SQMC"}; operations: '
                                  + ", ".join( sorted( READS ) + sorted( WRITES )))
        what = request[ "supervisor" ].removeprefix( "supervisor." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"the machine supervisors do not offer { what[ :40 ]!r}" )
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        unknown = set( request ) - set( ALLOWS[ what ]) - { "supervisor" }
        if unknown:
            return self._refused( f"{ what } does not take: " + ", ".join( sorted( str( k )[ :20 ] for k in unknown ))
                                  + f"; it takes { self._takes( what ) }, e.g. { self._example( what ) }" )
        flags: dict[ str, str ] = {}
        for key, ( flag, pattern ) in FLAGS.items():
            value = request.get( key )
            if value is None or value == "":
                continue
            if not isinstance( value, str ) or not re.fullmatch( pattern, value.strip()):
                return self._refused( f"{ key } is not a valid value" )
            flags[ flag ] = value.strip()
        if what in WRITES:
            text = request.get( "text" )
            if not isinstance( text, str ) or not text.strip() or len( text ) > TEXT_MAX or "\x00" in text:
                return self._refused( f"a todo needs text, at most { TEXT_MAX } characters" )
            flags[ "text" ] = text.strip()
        missing = [ k for k in NEEDS[ what ] if FLAGS.get( k, ( k, ))[ 0 ] not in flags ]
        if missing:
            return self._refused( f"{ what } needs " + ", ".join( missing ))
        flags[ "task-id" ] = task.task_id
        flags[ "client" ] = task.sender
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( self._commands.start( what, flags ), what, keep_alive )
        except DeployCommandFailed as exc:
            return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, retryable=what in READS,
                                 summary=f"the machine supervisor ADW gave no outcome ({ exc })"[ :300 ],
                                 details={ "supervisor": what, "ok": False, "verified": False })
        if signal == "lost" and what in READS:
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        outcome = self._outcome( answer, what )
        if what in WRITES and outcome.kind == OutcomeKind.SUCCEEDED:
            outcome = self._handed_over( outcome, answer, flags[ "machine" ])
        return outcome

    def _wait( self, running: IRunningDeployCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a todo being handed over runs to its end, so it
        is never both cancelled here and queued there."""
        deadline, seen = self._clock.monotonic() + self._deadline, "continue"
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, seen
            if self._clock.monotonic() >= deadline:
                running.stop()
                raise DeployCommandFailed( "timed out" )
            signal = keep_alive()
            if signal != "continue":
                seen = signal
                if what in READS:
                    running.stop()
                    return {}, signal
            self._clock.sleep( self._poll )

    @staticmethod
    def _outcome( answer: dict, what: str ) -> AgentOutcome:
        data     = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        line     = str( answer.get( "line" ) or "" )[ :1000 ]
        ok       = bool( answer.get( "ok" ))
        verified = ok and answer.get( "verified" ) is True
        details  = { "supervisor": what, "ok": ok, "verified": verified, "retryable": bool( answer.get( "retryable" )),
                     "adw_id": answer.get( "adw_id" )}
        if what == "read" and verified:     # where a handed-over todo now is: the gateway's proof it finished
            message = data.get( "message" ) if isinstance( data.get( "message" ), dict ) else {}
            details[ "handoff_state" ] = { "id": str( message.get( "id" ) or "" ), "box": str( data.get( "box" ) or "" ),
                                           "machine": str( data.get( "machine" ) or "" ),
                                           "result": str( message.get( "result" ) or "" )[ :500 ],
                                           "error": str( message.get( "error" ) or "" )[ :500 ]}
        body     = line + ( "\n\n" + json.dumps( data, indent=1, default=str )[ :12000 ] if data else "" )
        if verified:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body, details=details )
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=( line or "the supervisor operation did not complete" )[ :300 ],
                             body=body, retryable=bool( answer.get( "retryable" )), details=details )

    @staticmethod
    def _handed_over( outcome: AgentOutcome, answer: dict, machine: str ) -> AgentOutcome:
        """A queued todo is accepted, not done: name the read that will show it finished."""
        data = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        todo = str( data.get( "id" ) or "" )
        if not re.fullmatch( FLAGS[ "id" ][ 1 ], todo ):
            return outcome
        where = str( data.get( "machine" ) or machine )
        check = { "supervisor": "read", "machine": where, "id": todo }
        handoff = { "agent": "machine-supervisor", "operation": "inspect", "prompt": check,
                    "done": f"{ todo } is in completed", "failed": f"{ todo } is in failed",
                    "what": f"todo { todo } on { where }" }
        return outcome.model_copy( update={ "details": { **( outcome.details or {}), "handoff": handoff },
                                            "summary": f"{ outcome.summary } (queued, not done yet)"[ :300 ]})

    @staticmethod
    def _takes( what: str ) -> str:
        return ", ".join( ALLOWS[ what ]) or "nothing else"

    @staticmethod
    def _example( what: str ) -> str:
        """The one correct prompt for `what`, so a refused requester can resend it once without guessing."""
        sample = { "machine": "SQMC", "box": "new", "id": "<message id>", "text": "<what to do>" }
        return json.dumps({ "supervisor": what, **{ key: sample[ key ] for key in ALLOWS[ what ]}})

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )
