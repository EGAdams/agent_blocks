"""BatiDesktopAgent: the IAgentAdapter that lets ChatGPT and Toyota drive the debug Chrome on BATI's own desktop
(DESKTOP-SHDBATI), through `adws/adw_bati_chrome.py`. No model in the path. Read `adws/bati_desktop/DESIGN.md`.

The prompt is JSON written by code (agent `bati-desktop`, workspace `bati-desktop`):

    {"bati": "status",         "machine": "BATI"}
    {"bati": "read",           "machine": "BATI", "view": "plan-for-today"}
    {"bati": "plan_for_today", "machine": "BATI"}

    inspect    read_only        status | read | screenshot
    implement  workspace_write  open | show_view | plan_for_today

`machine` is required every time; a task naming another machine is refused here with both names. (The gateway
already offers a `bati-desktop` task only to BATI's own worker -- `worker_pins` -- so this is the second of two
checks; the ADW's own machine guard is the third.) `url` and `view` are passed through as values, and the ADW's
allowlists decide them: the dashboard origin only, the `plan-for-today` view only. There is no type, fill, close or
quit anywhere in the chain.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .cashy_agent import CashyCommandFailed as CommandFailed, Machine
from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER = "bati-desktop"
READS     = frozenset({ "status", "read", "screenshot" })
WRITES    = frozenset({ "open", "show_view", "plan_for_today" })
TAKES     = { "url": frozenset({ "open", "plan_for_today" }), "view": frozenset({ "show_view", "read" })}
FIELDS    = frozenset({ "bati", "machine", "url", "view" })


class IRunningCommand( Protocol ):
    def poll( self ) -> dict | None: ...        # None while running; the ADW's Outcome once done
    def stop( self ) -> None: ...


class IBatiCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ]) -> IRunningCommand: ...


class BatiDesktopAgent:
    def __init__( self, commands: IBatiCommands, clock: IClock, machine: Machine, poll_seconds: float = 1.0,
                  deadline_seconds: float = 150.0 ) -> None:
        self._commands, self._clock, self._machine = commands, clock, machine
        self._poll, self._deadline = poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "bati" ), str ):
            return self._refused( 'a BATI desktop task is JSON, e.g. {"bati": "status", "machine": "'
                                  + self._machine.alias + '"}; operations: ' + ", ".join( sorted( READS ) + sorted( WRITES )))
        what     = request[ "bati" ]
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"the BATI desktop does not offer { what[ :40 ]!r} over the Bridge" )
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        unknown = set( request ) - FIELDS
        if unknown:
            return self._refused( "unknown field(s): " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        machine = request.get( "machine" )
        if not isinstance( machine, str ) or not machine.strip():
            return self._refused( f'name the machine every time: "machine": "{ self._machine.alias }"' )
        if not self._machine.named( machine ):
            return self._refused( f"this task names machine { machine[ :40 ]!r}, but this worker is "
                                  f"{ self._machine.alias } ({ self._machine.hostname }); nothing was opened here" )
        if task.agent != self._machine.agent:
            return self._refused( f"agent { task.agent } is not this machine's desktop agent ({ self._machine.agent })" )
        flags: dict[ str, str ] = {}
        for name, ops in TAKES.items():
            if name not in request:
                continue
            value = request[ name ]
            if what not in ops:
                return self._refused( f"{ what } takes no { name }" )
            if not isinstance( value, str ) or not value or len( value ) > 300 or value.startswith( "-" ):
                return self._refused( f"{ name } must be a short string" )
            flags[ name ] = value
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( self._commands.start( what, flags ), what, keep_alive )
        except CommandFailed as exc:
            return self._outcome({ "ok": False, "cause": "no_outcome", "message": f"the BATI Chrome ADW gave no outcome ({ exc })" },
                                 what, task.sender )
        if signal == "lost" and what in READS:
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( answer, what, task.sender )

    def _wait( self, running: IRunningCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a step that has started runs to its end."""
        deadline, seen = self._clock.monotonic() + self._deadline, "continue"
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, seen
            if self._clock.monotonic() >= deadline:
                running.stop()
                raise CommandFailed( "timed out" )
            signal = keep_alive()
            if signal != "continue":
                seen = signal
                if what in READS:
                    running.stop()
                    return {}, signal
            self._clock.sleep( self._poll )

    def _where( self ) -> dict:
        return { "hostname": self._machine.hostname, "alias": self._machine.alias, "agent": self._machine.agent }

    def _outcome( self, answer: dict, what: str, actor: str ) -> AgentOutcome:
        ok      = bool( answer.get( "ok" ))
        line    = str( answer.get( "message" ) or "" )[ :1000 ]
        details = { "bati": what, "ok": ok, "verified": bool( answer.get( "verified" )), "cause": answer.get( "cause", "" ),
                    "adw_id": answer.get( "adw_id", "" ), "machine": self._where(), "actor": actor,
                    "started": answer.get( "started" ), "finished": answer.get( "finished" ),
                    "data": answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}}
        where   = f"[{ self._machine.alias } { self._machine.hostname } adw { details[ 'adw_id' ] or '?' }]"
        body    = f"{ line }\n\n{ where }\n\n" + json.dumps( details, indent=1, default=str )[ :12000 ]
        summary = f"{ where } { line }".strip()[ :300 ]
        kind    = OutcomeKind.SUCCEEDED if ok else OutcomeKind.FAILED
        return AgentOutcome( kind=kind, performer=PERFORMER, summary=summary, body=body, retryable=False, details=details )

    def _refused( self, summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER,
                             summary=f"refused by the worker on { self._machine.alias } ({ self._machine.hostname }): { summary }",
                             details={ "machine": self._where()})
