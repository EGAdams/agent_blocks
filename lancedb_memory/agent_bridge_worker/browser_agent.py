"""BrowserAgent: the IAgentAdapter that lets ChatGPT and Toyota drive a browser on the Windows desktop. No model in the path.

The prompt is JSON written by code (ChatGPT turns "open the browser" into it; Toyota's plan does the same):

    {"browser": "open", "url": "https://example.com", "target": "chrome"}   # Chrome is the only authorized browser

    inspect    read_only        status | tabs | read | screenshot
    implement  workspace_write  open | navigate | refresh | select_tab | click | close | dashboard_notes   (workspace `browser`)

`dashboard_notes` is the whole "open the browser and go to Notes": Chrome on the desktop, the ROL dashboard,
the NOTES tab pressed, and the page read back to confirm NOTES is the active tab. It takes no fields.

Each task becomes one run of `adws/adw_browser_control.py` (through `IBrowserCommands`), which is the Browser
Control ADW: it has its own execution id (the ADW id), lifecycle log and object trace. This adapter only checks the
request against a fixed table, never lets a value become an argv option, and turns the ADW's one JSON outcome into
an honest AgentOutcome: success means the ADW verified the operation, never that it was queued.

A click whose words look consequential (pay, send, delete ...) is refused by the ADW unless the request carries
`"confirm": true`, which ChatGPT may set only after EG said so. Nothing here reads cookies, storage or passwords.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER = "browser_control"
READS     = frozenset({ "status", "tabs", "read", "screenshot" })
WRITES    = frozenset({ "open", "navigate", "refresh", "select_tab", "click", "close", "dashboard_notes" })
#: request key -> the ADW flag it becomes. `target` is the browser (Chrome only); `browser` already names the operation.
FLAGS     = { "target": "browser", "url": "url", "tab": "tab", "text": "text", "label": "label", "css": "css" }
REQUIRED  = { "navigate": ("url",), "select_tab": ("tab",), "close": ("tab",) }
MAX_VALUE = 500


class BrowserCommandFailed( Exception ):
    """The Browser Control ADW gave no outcome (it crashed, timed out, or wrote nothing)."""


class IRunningBrowserCommand( Protocol ):
    def poll( self ) -> dict | None: ...        # None while running; the ADW's outcome once done
    def stop( self ) -> None: ...


class IBrowserCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ], confirm: bool ) -> IRunningBrowserCommand: ...


class BrowserAgent:
    def __init__( self, commands: IBrowserCommands, clock: IClock, poll_seconds: float = 0.5, deadline_seconds: float = 120.0 ) -> None:
        self._commands, self._clock, self._poll, self._deadline = commands, clock, poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "browser" ), str ):
            return self._refused( 'a browser task is JSON, e.g. {"browser": "open", "url": "https://example.com"}; operations: '
                                  + ", ".join( sorted( READS ) + sorted( WRITES )))
        what = request[ "browser" ].removeprefix( "browser." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"the browser does not offer { what!r}" )
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        if str( request.get( "target", "chrome" )).strip().lower() not in ( "chrome", "default", "google chrome" ):
            return self._refused( "Chrome is the only authorized browser; no other browser is launched" )
        unknown = set( request ) - set( FLAGS ) - { "browser", "confirm" }
        if unknown:
            return self._refused( "unknown field(s): " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        flags: dict[ str, str ] = {}
        for key, flag in FLAGS.items():
            value = request.get( key )
            if value is None or value == "":
                continue
            if not isinstance( value, ( str, int )) or isinstance( value, bool ) or len( str( value )) > MAX_VALUE:
                return self._refused( f"{ key } must be short text" )
            flags[ flag ] = str( value )
        missing = [ k for k in REQUIRED.get( what, ()) if FLAGS[ k ] not in flags ]
        if missing:
            return self._refused( f"{ what } needs: " + ", ".join( missing ))
        if what == "click" and not any( k in flags for k in ( "text", "label", "css" )):
            return self._refused( "click needs text, label or css" )
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( self._commands.start( what, flags, request.get( "confirm" ) is True ), what, keep_alive )
        except BrowserCommandFailed as exc:
            return self._failed( f"the Browser Control ADW gave no outcome ({ exc })", what )
        if signal == "lost":
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( answer, what )

    def _wait( self, running: IRunningBrowserCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a change that has started runs to its end."""
        deadline, seen = self._clock.monotonic() + self._deadline, "continue"
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, seen
            if self._clock.monotonic() >= deadline:
                running.stop()
                raise BrowserCommandFailed( "timed out" )
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
        verified = bool( answer.get( "ok" )) and data.get( "verified" ) is True
        details  = { "browser": what, "ok": bool( answer.get( "ok" )), "verified": verified, "adw_id": answer.get( "adw_id" ), **data }
        body     = line + ( "\n\n" + str( data.get( "text" )) if what == "read" and data.get( "text" ) else "" )
        if verified:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body, details=details )
        # A refusal (consequential click, no such tab) is final: repeating the same request gives the same answer.
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=( line or "the browser operation did not complete" )[ :300 ],
                             body=body, retryable=False, details=details )

    @staticmethod
    def _failed( summary: str, what: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ], body=summary, retryable=False,
                             details={ "browser": what, "ok": False, "verified": False } )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )
