"""TriageAgent: the IAgentAdapter that lets the Star Trek Bridge read Failure Triage's ledger on SQMC, record
a fix, and send a cluster to its owner again. No model in this path.

The prompt is JSON written by code (`submit_agent_task` to agent and workspace `failure-triage`):

    {"triage": "status"}
    {"triage": "clusters", "state": "open"}
    {"triage": "cluster", "cluster": "3f2a9c"}
    {"triage": "fix", "cluster": "3f2a9c", "fix": "7fa5717 Machine Ops: unit no longer required"}
    {"triage": "reroute", "cluster": "3f2a9c"}

    inspect    read_only        status | clusters | cluster
    implement  workspace_write  fix | reroute              (the ledger only; no failed envelope is touched)

Each task is one run of `adws/adw_failure_triage_status.py` or `adws/adw_failure_triage_fix.py`. A request
names a state and a cluster id -- never a path -- and each value is checked here before it becomes a
`--flag=value`. A fix is short prose that lands in the ledger and the mechanic's case history; it is one argv
item and is never run.
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

PERFORMER = "failure_triage_adw"
READS     = frozenset({ "status", "clusters", "cluster" })
WRITES    = frozenset({ "fix", "reroute" })
FIX_MAX   = 400
FLAGS     = { "state":   r"(open|routed|fixed|closed)",
              "cluster": r"[0-9a-f]{4,12}" }
NEEDS     = { "status": (), "clusters": (), "cluster": ( "cluster", ), "fix": ( "cluster", "fix" ), "reroute": ( "cluster", )}
ALLOWS    = { "status": (), "clusters": ( "state", ), "cluster": ( "cluster", ), "fix": ( "cluster", "fix" ),
              "reroute": ( "cluster", )}


class ITriageCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ]) -> IRunningDeployCommand: ...


class TriageAgent:
    def __init__( self, commands: ITriageCommands, clock: IClock, poll_seconds: float = 1.0,
                  deadline_seconds: float = 120.0 ) -> None:
        self._commands, self._clock, self._poll, self._deadline = commands, clock, poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "triage" ), str ):
            return self._refused( 'a triage task is JSON, e.g. {"triage": "status"}; operations: '
                                  + ", ".join( sorted( READS ) + sorted( WRITES )))
        what = request[ "triage" ].removeprefix( "triage." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"failure triage does not offer { what[ :40 ]!r}" )
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        unknown = set( request ) - set( ALLOWS[ what ]) - { "triage" }
        if unknown:
            return self._refused( f"{ what } does not take: " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        flags: dict[ str, str ] = {}
        for key, pattern in FLAGS.items():
            value = request.get( key )
            if value is None or value == "":
                continue
            if not isinstance( value, str ) or not re.fullmatch( pattern, value.strip()):
                return self._refused( f"{ key } is not a valid value" )
            flags[ key ] = value.strip()
        if "fix" in request:
            fix = request[ "fix" ]
            if not isinstance( fix, str ) or len( fix.strip()) < 5 or len( fix ) > FIX_MAX or "\x00" in fix:
                return self._refused( f"a fix is 5 to { FIX_MAX } characters: the commit and what it changed" )
            flags[ "fix" ] = " ".join( fix.split())
        missing = [ k for k in NEEDS[ what ] if k not in flags ]
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
                                 summary=f"the failure triage ADW gave no outcome ({ exc })"[ :300 ],
                                 details={ "triage": what, "ok": False, "verified": False })
        if signal == "lost" and what in READS:
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( answer, what )

    def _wait( self, running: IRunningDeployCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a write is one ledger save and runs to its end."""
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
        details  = { "triage": what, "ok": ok, "verified": verified, "adw_id": answer.get( "adw_id" )}
        body     = line + ( "\n\n" + json.dumps( data, indent=1, default=str )[ :12000 ] if data else "" )
        if verified:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body, details=details )
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=( line or "the triage operation did not complete" )[ :300 ],
                             body=body, retryable=False, details=details )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )
