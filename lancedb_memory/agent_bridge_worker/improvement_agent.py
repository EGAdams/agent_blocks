"""ImprovementAgent: the IAgentAdapter that lets the Star Trek Bridge drive one agent-improvement cycle
(`adws/adw_improvement_cycle.py`) and the operations on it (`adws/adw_improvement_ops.py`). No model in this path;
the improver inside a cycle is the ADW's business, not the worker's.

The prompt is JSON written by code (`submit_agent_task` to agent and workspace `agent-improvement`):

    {"improvement": "start",      "target": "sydney", "environment": "pilot"}
    {"improvement": "start",      "target": "sydney", "environment": "pilot", "cycle_id": "<id>"}      (resume)
    {"improvement": "status",     "target": "sydney", "cycle_id": "<id>"}
    {"improvement": "proposal" | "evaluation" | "report" | "health", "cycle_id": "<id>"}
    {"improvement": "lineage",    "target": "sydney"[, "cycle_id": "<id>"]}
    {"improvement": "decide",     "cycle_id": "<id>", "approve": true, "reason": "..."}
    {"improvement": "cancel",     "cycle_id": "<id>"}
    {"improvement": "rollback",   "cycle_id": "<id>", "reason": "..."}

    inspect    read_only        status | proposal | evaluation | report | lineage | health
    implement  workspace_write  start | decide | cancel | rollback

Identity comes from the Bridge, never from the prompt: the sender is passed as `--requested-by`, `--approver`
or `--by`, and the receiving promotion policy decides whether that sender may approve or roll back at all.
`PEOPLE` names the Bridge identities that are a person: `chatgpt-bridge` is EG's own ChatGPT connector (EG,
2026-11-16: "EG counts as me"), so its decide and rollback are recorded as `EG`, with the channel in the reason.
A target agent's own Bridge identity may read its cycles but never decide, roll back or start one: no agent
judges its own changes. The Bridge task id is the idempotency key -- a repeated `start` returns the same cycle
and a repeated `decide` is recorded once -- and it is passed as `--task-id` so every SSSF event carries it.

A `start` runs as long as the cycle does. A Bridge cancel during it is handed to the cycle as a cancel request,
which the engine honours at the next phase boundary, so a cycle is never killed halfway through a write.
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

PERFORMER = "agent_improvement_adw"
READS     = frozenset({ "status", "proposal", "evaluation", "report", "lineage", "health" })
WRITES    = frozenset({ "start", "decide", "cancel", "rollback" })
REASON_MAX = 500
#: Bridge identities of the agents a cycle may improve. They may read; they may not start, decide or roll back.
TARGET_SENDERS = { "sydney": "sydney-bridge", "mazda": "mazda-bridge" }
#: Bridge identities that stand for a person. Any other sender is recorded under its own name.
PEOPLE = { "chatgpt-bridge": "EG" }
FLAGS = { "target":       ( "target", r"[a-z][a-z0-9_-]{0,30}" ),
          "environment":  ( "environment", r"(pilot|live)" ),
          "cycle_id":     ( "cycle-id", r"[a-z][a-z0-9_-]{0,30}-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}|[a-z0-9][a-z0-9_-]{0,60}" ),
          "parent_cycle": ( "parent-cycle", r"[a-z0-9][a-z0-9_-]{0,80}" )}
ALLOWS = { "start":    ( "target", "environment", "cycle_id", "parent_cycle" ),
           "decide":   ( "target", "environment", "cycle_id", "approve", "reason" ),
           "cancel":   ( "target", "environment", "cycle_id" ),
           "rollback": ( "target", "environment", "cycle_id", "reason" ),
           "lineage":  ( "target", "environment", "cycle_id" ),
           **{ op: ( "target", "environment", "cycle_id" ) for op in ( "status", "proposal", "evaluation", "report", "health" )}}
NEEDS  = { "decide": ( "cycle-id", ), "cancel": ( "cycle-id", ), "rollback": ( "cycle-id", ),
           **{ op: ( "cycle-id", ) for op in ( "status", "proposal", "evaluation", "report", "health" )}}


class IImprovementCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ]) -> IRunningDeployCommand: ...


class ImprovementAgent:
    def __init__( self, commands: IImprovementCommands, clock: IClock, poll_seconds: float = 2.0,
                  read_deadline: float = 120.0, cycle_deadline: float = 5400.0 ) -> None:
        self._commands, self._clock, self._poll = commands, clock, poll_seconds
        self._read_deadline, self._cycle_deadline = read_deadline, cycle_deadline

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "improvement" ), str ):
            return self._refused( 'an improvement task is JSON, e.g. {"improvement": "status", "cycle_id": "..."}; '
                                  "operations: " + ", ".join( sorted( READS ) + sorted( WRITES )))
        what = request[ "improvement" ].removeprefix( "improvement." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"agent improvement does not offer { what[ :40 ]!r}" )
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        unknown = set( request ) - set( ALLOWS[ what ]) - { "improvement" }
        if unknown:
            return self._refused( f"{ what } does not take: " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        flags: dict[ str, str ] = {}
        for key, ( flag, pattern ) in FLAGS.items():
            value = request.get( key )
            if value is None or value == "" or key not in ALLOWS[ what ]:
                continue
            if not isinstance( value, str ) or not re.fullmatch( pattern, value.strip()):
                return self._refused( f"{ key } is not a valid value" )
            flags[ flag ] = value.strip()
        target = flags.get( "target", "sydney" )
        if what in WRITES and task.sender == TARGET_SENDERS.get( target ):
            return self._refused( f"{ task.sender } may read { target }'s cycles but not { what } one: "
                                  "no agent judges her own changes" )
        missing = [ flag for flag in NEEDS.get( what, ()) if flag not in flags ]
        if missing:
            return self._refused( f"{ what } needs " + ", ".join( m.replace( "-", "_" ) for m in missing ))
        if what == "decide":
            if not isinstance( request.get( "approve" ), bool ):
                return self._refused( "decide needs approve: true or false" )
            flags[ "approve" if request[ "approve" ] else "reject" ] = ""
            flags[ "approver" ], flags[ "key" ] = PEOPLE.get( task.sender, task.sender ), task.task_id
        if what in ( "decide", "rollback" ):
            reason = request.get( "reason", "" )
            if not isinstance( reason, str ) or len( reason ) > REASON_MAX or "\x00" in reason:
                return self._refused( f"reason is text, at most { REASON_MAX } characters" )
            if task.sender in PEOPLE:
                reason = f"{ reason.strip()} (via { task.sender })".strip()
            if reason.strip():
                flags[ "reason" ] = reason.strip()
        if what in ( "cancel", "rollback" ):
            flags[ "by" ] = PEOPLE.get( task.sender, task.sender )
        if what == "start":
            flags[ "requested-by" ], flags[ "request-key" ] = task.sender, task.task_id
        flags[ "task-id" ] = task.task_id
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( what, flags, keep_alive )
        except DeployCommandFailed as exc:
            return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, retryable=what in READS or what == "start",
                                 summary=f"the improvement ADW gave no outcome ({ exc })"[ :300 ],
                                 details={ "improvement": what, "ok": False, "verified": False })
        if signal == "lost" and what in READS:
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( answer, what )

    def _wait( self, what: str, flags: dict[ str, str ], keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped. A start is asked to cancel (once) and runs on to
        the phase boundary where the engine stops it; a decide, cancel or rollback runs to its end."""
        running  = self._commands.start( what, flags )
        deadline = self._clock.monotonic() + ( self._cycle_deadline if what == "start" else self._read_deadline )
        seen, asked = "continue", False
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, seen
            if self._clock.monotonic() >= deadline:
                if what == "start" and "cycle-id" in flags:
                    self._commands.start( "cancel", { "cycle-id": flags[ "cycle-id" ], "by": "bridge-worker-timeout",
                                                      "task-id": flags[ "task-id" ]})
                running.stop()
                raise DeployCommandFailed( "timed out" )
            signal = keep_alive()
            if signal != "continue":
                seen = signal
                if what in READS:
                    running.stop()
                    return {}, signal
                if what == "start" and not asked and "cycle-id" in flags:
                    self._commands.start( "cancel", { "cycle-id": flags[ "cycle-id" ], "by": flags[ "requested-by" ],
                                                      "task-id": flags[ "task-id" ]})
                    asked = True
            self._clock.sleep( self._poll )

    @staticmethod
    def _outcome( answer: dict, what: str ) -> AgentOutcome:
        data     = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        line     = str( answer.get( "line" ) or "" )[ :2000 ]
        ok       = bool( answer.get( "ok" ))
        verified = ok and answer.get( "verified" ) is True
        details  = { "improvement": what, "ok": ok, "verified": verified, "retryable": bool( answer.get( "retryable" )),
                     "adw_id": answer.get( "adw_id" ) or data.get( "adw_id" ), "cycle_id": data.get( "cycle_id" ),
                     "state": data.get( "state" )}
        body     = line + ( "\n\n" + json.dumps( data, indent=1, default=str )[ :12000 ] if data else "" )
        if verified:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body,
                                 details=details )
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER,
                             summary=( line or "the improvement operation did not complete" )[ :300 ], body=body,
                             retryable=bool( answer.get( "retryable" )), details=details )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )
