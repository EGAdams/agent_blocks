"""ScalaStorageAgent: the IAgentAdapter that lets ChatGPT and Toyota file, find and verify scans on BATI, R46 and
SQMC through Scala. No model in the path.

The prompt is JSON written by code (ChatGPT's `submit_agent_task` to agent and workspace `scala-storage`):

    {"storage": "targets"}
    {"storage": "folders", "machine": "BATI"}
    {"storage": "store", "scan_id": "scan_01M787R1STEA7N30DM8PG13MSZ", "machine": "BATI", "folder": "insurance/home"}
    {"storage": "status", "scan_id": "scan_..."}

    inspect    read_only        targets | folders | health | verify | retrieve | status
    implement  workspace_write  store                                         (always a copy; the original stays)

Each task is one run of `adws/adw_scala_storage.py`, with its own ADW id, run record and audit lines. A request
names a machine, a folder and a scan id -- never a path on the Bridge machine, never a command -- and each value
is checked here before it becomes a `--flag=value`. The ADW and the helper it ships to the destination check
them all again (approved folder, identity, no symlinks, SHA-256). A store that has started runs to its end, so a
cancel never leaves a half-written file; a destination that cannot be reached is a FAILED, retryable outcome with
a staged copy kept for the retry.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from .deploy_agent import DeployCommandFailed, IRunningDeployCommand
from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal
from typing import Protocol

PERFORMER = "scala_storage_adw"
READS     = frozenset({ "targets", "folders", "health", "verify", "retrieve", "status" })
WRITES    = frozenset({ "store" })
#: request key -> ADW flag, and the pattern its value must match. Folders may be spoken loosely
#: ("insurance/home", "/home/adamsl/file_cabinet/insurance/home/"); the ADW decides what is approved.
FLAGS     = { "scan_id": ( "scan-id", r"(scan_[0-9A-HJKMNP-TV-Z]{26}|last|latest)" ),
              "machine": ( "machine", r"[A-Za-z0-9][A-Za-z0-9 '’_-]{0,40}" ),
              "folder":  ( "folder", r"[A-Za-z0-9_/. -]{1,200}" )}
NEEDS     = { "folders": ( "machine", ), "health": ( "machine", ), "store": ( "scan_id", "machine", "folder" ),
              "verify": ( "scan_id", "machine", "folder" ), "retrieve": ( "scan_id", "machine" ), "targets": (), "status": ()}


class IScalaStorageCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ]) -> IRunningDeployCommand: ...


class ScalaStorageAgent:
    def __init__( self, commands: IScalaStorageCommands, clock: IClock, poll_seconds: float = 1.0,
                  deadline_seconds: float = 900.0 ) -> None:
        self._commands, self._clock, self._poll, self._deadline = commands, clock, poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "storage" ), str ):
            return self._refused( 'a storage task is JSON, e.g. {"storage": "folders", "machine": "BATI"}; operations: '
                                  + ", ".join( sorted( READS ) + sorted( WRITES )))
        what = request[ "storage" ].removeprefix( "storage." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"Scala storage does not offer { what!r}" + ( "; Scala only copies" if what in ( "move", "delete" ) else "" ))
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        unknown = set( request ) - set( FLAGS ) - { "storage" }
        if unknown:
            return self._refused( "unknown field(s): " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        flags: dict[ str, str ] = {}
        for key, ( flag, pattern ) in FLAGS.items():
            value = request.get( key )
            if value is None or value == "":
                continue
            if not isinstance( value, str ) or not re.fullmatch( pattern, value.strip()) or ".." in value:
                return self._refused( f"{ key } is not a valid value" )
            flags[ flag ] = value.strip()
        missing = [ k for k in NEEDS[ what ] if FLAGS[ k ][ 0 ] not in flags ]
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
                                 summary=f"the Scala storage ADW gave no outcome ({ exc })"[ :300 ],
                                 details={ "storage": what, "ok": False, "verified": False })
        if signal == "lost" and what in READS:
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( answer, what )

    def _wait( self, running: IRunningDeployCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a store that has started runs to its end."""
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
        details  = { "storage": what, "ok": ok, "verified": verified, "retryable": bool( answer.get( "retryable" )),
                     "adw_id": answer.get( "adw_id" ), **data }
        body     = line + ( "\n\n" + json.dumps( data, indent=1, default=str )[ :12000 ] if data else "" )
        if verified:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body, details=details )
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=( line or "the storage operation did not complete" )[ :300 ],
                             body=body, retryable=bool( answer.get( "retryable" )), details=details )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )
