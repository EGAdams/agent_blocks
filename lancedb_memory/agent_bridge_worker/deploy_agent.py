"""DeployAgent: the IAgentAdapter that lets ChatGPT and Toyota deploy, restart, check and roll back EG's projects.
No model in the path.

The prompt is JSON written by code (ChatGPT's `submit_agent_task` to agent `deploy`; Toyota's plan does the same):

    {"deploy": "status", "project": "dashboard"}
    {"deploy": "deploy", "project": "dashboard", "reason": "Notes: the active warning blinks", "dry_run": true}

    inspect    read_only        projects | status | health | logs | history
    implement  workspace_write  deploy | restart | rollback                                  (workspace `deploy`)

Each task becomes one run of `adws/adw_deploy.py` (through `IDeployCommands`), which is the Deployment ADW: it has
its own ADW id, a run record of every step, and a ledger line. The projects, their commands, services and health
checks are a fixed table in `adws/deployment/projects.json`; a request names a project, never a path or a command,
and this adapter never lets a value become an argv option. Success means the ADW verified the result with the
project's own health checks, never that something was started. A change that has begun is never stopped halfway:
a cancel or a lost lease stops a read, not a deploy, a restart or a rollback.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER = "deployment_adw"
READS     = frozenset({ "projects", "status", "health", "logs", "history" })
WRITES    = frozenset({ "deploy", "restart", "rollback" })
#: request key -> the ADW flag it becomes, and the pattern its value must match.
FLAGS     = { "project": ( "project", r"[a-z][a-z0-9_-]{0,40}" ), "release_id": ( "release-id", r"[A-Za-z0-9_-]{1,80}" ),
              "unit": ( "unit", r"[A-Za-z0-9@._-]{1,80}" ), "lines": ( "lines", r"[0-9]{1,3}" ),
              "reason": ( "reason", r"[^\x00-\x08\x0b-\x1f]{1,300}" )}
NO_PROJECT = frozenset({ "projects" })


class DeployCommandFailed( Exception ):
    """The Deployment ADW gave no outcome (it crashed, timed out, or wrote nothing)."""


class IRunningDeployCommand( Protocol ):
    def poll( self ) -> dict | None: ...        # None while running; the ADW's outcome once done
    def stop( self ) -> None: ...


class IDeployCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ], dry_run: bool ) -> IRunningDeployCommand: ...


class DeployAgent:
    def __init__( self, commands: IDeployCommands, clock: IClock, poll_seconds: float = 1.0,
                  deadline_seconds: float = 1500.0 ) -> None:
        self._commands, self._clock, self._poll, self._deadline = commands, clock, poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "deploy" ), str ):
            return self._refused( 'a deploy task is JSON, e.g. {"deploy": "status", "project": "dashboard"}; operations: '
                                  + ", ".join( sorted( READS ) + sorted( WRITES )))
        what = request[ "deploy" ].removeprefix( "deploy." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"the Deployment ADW does not offer { what!r}" )
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        unknown = set( request ) - set( FLAGS ) - { "deploy", "dry_run" }
        if unknown:
            return self._refused( "unknown field(s): " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        flags: dict[ str, str ] = {}
        for key, ( flag, pattern ) in FLAGS.items():
            value = request.get( key )
            if value is None or value == "":
                continue
            if isinstance( value, bool ) or not isinstance( value, ( str, int )) or not re.fullmatch( pattern, str( value )):
                return self._refused( f"{ key } is not a valid value" )
            flags[ flag ] = str( value )
        if what not in NO_PROJECT and "project" not in flags:
            return self._refused( f"{ what } needs a project; ask for projects to see them" )
        if what in ( "deploy", "rollback" ) and "reason" not in flags and request.get( "dry_run" ) is not True:
            return self._refused( f"{ what } needs a reason, so the record shows why" )
        flags[ "task-id" ] = task.task_id
        flags[ "client" ] = task.sender
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( self._commands.start( what, flags, request.get( "dry_run" ) is True ), what, keep_alive )
        except DeployCommandFailed as exc:
            return self._failed( f"the Deployment ADW gave no outcome ({ exc })", what )
        if signal == "lost" and what in READS:
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( answer, what, request.get( "dry_run" ) is True )

    def _wait( self, running: IRunningDeployCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a change that has started runs to its end."""
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
    def _outcome( answer: dict, what: str, dry_run: bool = False ) -> AgentOutcome:
        data     = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        line     = str( answer.get( "line" ) or "" )[ :1000 ]
        ok       = bool( answer.get( "ok" ))
        verified = ok and answer.get( "verified" ) is True
        details  = { "deploy": what, "ok": ok, "verified": verified, "adw_id": answer.get( "adw_id" ) or data.get( "adw_id" ), **data }
        body     = line
        if what == "logs" and data.get( "text" ):
            body += "\n\n" + str( data[ "text" ])[ -12000: ]
        elif data:
            body += "\n\n" + json.dumps({ k: v for k, v in data.items() if k != "text" }, indent=1, default=str )[ :12000 ]
        if ok and ( verified or dry_run ):              # a dry run changes nothing, so there is nothing to verify
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body, details=details )
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=( line or "the deployment operation did not complete" )[ :300 ],
                             body=body, retryable=False, details=details )

    @staticmethod
    def _failed( summary: str, what: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ], body=summary, retryable=False,
                             details={ "deploy": what, "ok": False, "verified": False } )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )
