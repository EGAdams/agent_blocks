"""CashyMachineAgent: the IAgentAdapter that lets ChatGPT and Toyota drive Cashy on one named machine (R46 first).
No model in the path.

The prompt is JSON written by code (ChatGPT's `submit_agent_task` to agent `cashy-r46`, workspace `cashy-r46`):

    {"cashy": "status",       "machine": "R46", "session": "r46-bridge"}
    {"cashy": "open_bank",    "machine": "R46", "session": "r46-bridge", "bank": "fifth_third"}
    {"cashy": "logged_in",    "machine": "R46", "session": "r46-bridge"}      # only after EG says he is signed in
    {"cashy": "list_options", "machine": "R46", "session": "r46-bridge"}

    inspect    read_only        status | list_options | read_page
    implement  workspace_write  open_bank | logged_in | pause | resume | stop

`machine` and `session` are required, every time: a task that names another machine is refused here with both
names, and never runs on this one. (The gateway already offers a `cashy-r46` task only to R46's own worker --
`worker_pins` -- so this check is the second of two.) The session is Cashy's own, saved sanitised in SQLite on this
machine, so separate tasks continue the same tab.

Each task is one run of `adws/adw_cashy_<op>.py` (through `ICashyCommands`) with `--actor` set to the Bridge sender
(`chatgpt-bridge`, `toyota-bridge`, `sydney-bridge`), never `user`: Cashy lets only the user approve a consequential step, so a
Bridge caller can open and read a bank page but can never approve a transfer or a payment. Nothing here types,
reads or asks for a password or code; the person signs in by hand in the visible Chrome.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER = "cashy"
READS     = frozenset({ "status", "list_options", "read_page" })
WRITES    = frozenset({ "open_bank", "logged_in", "pause", "resume", "stop" })
#: the ADW each operation runs (read_page and list_options are the same step)
ADWS      = { "status": "status", "list_options": "read_page", "read_page": "read_page", "open_bank": "open_bank",
              "logged_in": "logged_in", "pause": "pause", "resume": "resume", "stop": "stop" }
SESSION   = re.compile( r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" )
BANK      = re.compile( r"[A-Za-z][A-Za-z0-9_ -]{0,40}" )
FIELDS    = frozenset({ "cashy", "machine", "session", "bank" })


class CashyCommandFailed( Exception ):
    """The Cashy ADW gave no outcome (it crashed, timed out, or wrote nothing)."""


class Machine( BaseModel ):
    """This machine, as the Bridge names it: the hostname and the short alias (`R46`) a request may use."""
    model_config = ConfigDict( frozen=True )

    hostname: str
    alias:    str
    agent:    str

    def named( self, value: str ) -> bool:
        return value.strip().lower() in { self.hostname.lower(), self.alias.lower() }

    @classmethod
    def load( cls, path: Path, hostname: str ) -> "Machine | None":
        """The `machines.json` entry for this host, or None when this host runs no machine-pinned agent."""
        table = json.loads( path.read_text( encoding="utf-8" ))
        entry = table.get( hostname.lower())
        return cls( hostname=hostname.lower(), **entry ) if isinstance( entry, dict ) else None


class IRunningCashyCommand( Protocol ):
    def poll( self ) -> dict | None: ...        # None while running; the ADW's outcome once done
    def stop( self ) -> None: ...


class ICashyCommands( Protocol ):
    def start( self, adw: str, flags: dict[ str, str ]) -> IRunningCashyCommand: ...


class ITabTitles( Protocol ):
    def title( self, target_id: str ) -> str: ...


class CashyMachineAgent:
    def __init__( self, commands: ICashyCommands, clock: IClock, machine: Machine, titles: ITabTitles | None = None,
                  poll_seconds: float = 1.0, deadline_seconds: float = 180.0 ) -> None:
        self._commands, self._clock, self._machine, self._titles = commands, clock, machine, titles
        self._poll, self._deadline = poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "cashy" ), str ):
            return self._refused( 'a Cashy task is JSON, e.g. {"cashy": "status", "machine": "'
                                  + self._machine.alias + '", "session": "r46-bridge"}; operations: '
                                  + ", ".join( sorted( READS ) + sorted( WRITES )))
        what     = request[ "cashy" ].removeprefix( "cashy." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"Cashy does not offer { what!r} over the Bridge" )
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
            return self._refused( f"agent { task.agent } is not this machine's Cashy ({ self._machine.agent })" )
        session = request.get( "session" )
        if not isinstance( session, str ) or not SESSION.fullmatch( session ):
            return self._refused( 'name the Cashy session every time, e.g. "session": "r46-bridge" '
                                  "(letters, digits, . _ -, at most 64)" )
        flags = { "session": session, "actor": task.sender }
        if what == "open_bank":
            bank = request.get( "bank" )
            if not isinstance( bank, str ) or not BANK.fullmatch( bank ):
                return self._refused( 'open_bank needs a bank, e.g. "bank": "fifth_third"' )
            flags.update({ "bank": bank, "launch_chrome": "true" })
        elif "bank" in request:
            return self._refused( f"{ what } takes no bank; the session already knows it" )
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( self._commands.start( ADWS[ what ], flags ), what, keep_alive )
        except CashyCommandFailed as exc:
            return self._failed( f"Cashy on { self._machine.alias } gave no outcome ({ exc })", what, session )
        if signal == "lost" and what in READS:
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( answer, what, session, task.sender )

    def _wait( self, running: IRunningCashyCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a step that has started runs to its end."""
        deadline, seen = self._clock.monotonic() + self._deadline, "continue"
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, seen
            if self._clock.monotonic() >= deadline:
                running.stop()
                raise CashyCommandFailed( "timed out" )
            signal = keep_alive()
            if signal != "continue":
                seen = signal
                if what in READS:
                    running.stop()
                    return {}, signal
            self._clock.sleep( self._poll )

    def _where( self ) -> dict:
        return { "hostname": self._machine.hostname, "alias": self._machine.alias, "agent": self._machine.agent }

    def _outcome( self, answer: dict, what: str, session: str, actor: str ) -> AgentOutcome:
        data    = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        line    = str( answer.get( "message" ) or answer.get( "line" ) or "" )[ :2000 ]
        ok      = bool( answer.get( "ok" ))
        saved   = data.get( "session" ) if isinstance( data.get( "session" ), dict ) else {}
        tab     = str( saved.get( "target_id" ) or "" )
        title   = ""
        if tab and self._titles is not None:
            try:
                title = mask( self._titles.title( tab ))[ :200 ]
            except Exception:                       # noqa: BLE001 -- a title is a nicety; the step's outcome stands
                title = ""
        details = { "cashy": what, "ok": ok, "machine": self._where(), "session": session, "actor": actor,
                    "state": data.get( "state" ) or saved.get( "state" ), "bank": saved.get( "bank", "" ),
                    "tab": tab, "url": saved.get( "url", "" ), "title": title,
                    "task": saved.get( "task", {}), "question": saved.get( "question", "" ),
                    "events": data.get( "events", [])[ -20: ]}
        where   = f"[{ self._machine.alias } { self._machine.hostname } session { session }: { details[ 'state' ] or '?' }]"
        body    = f"{ line }\n\n{ where }\n\n" + json.dumps( details, indent=1, default=str )[ :12000 ]
        summary = f"{ where } { line.splitlines()[ 0 ] if line else '' }".strip()[ :300 ]
        if ok:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=summary, body=body, details=details )
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary, body=body, retryable=False,
                             details=details )

    def _failed( self, summary: str, what: str, session: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ], body=summary, retryable=False,
                             details={ "cashy": what, "ok": False, "machine": self._where(), "session": session })

    def _refused( self, summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER,
                             summary=f"refused by the worker on { self._machine.alias } ({ self._machine.hostname }): { summary }",
                             details={ "machine": self._where() })


DIGITS = re.compile( r"\d{4,}" )


def mask( text: str ) -> str:
    """A tab title can hold an account number once signed in; keep the last four digits of any long run."""
    return DIGITS.sub( lambda m: "*" * ( len( m.group()) - 4 ) + m.group()[ -4: ], text )
