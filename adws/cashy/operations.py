"""Cashy's operations: the catalog the `adw_cashy_*.py` scripts, the Smart
Menu and the post office all run. Every one is in-process -- no script
launches another -- and every one goes through the same CashyAgent, so a
fine-grained ADW ("click Statements") and a sentence typed into the chat
reach the same State and the same command.

The agent is built on first use, from the run's --port, because a catalog
is made before its arguments are read.
"""
from __future__ import annotations

from typing import Any, Callable

from browser_workflows.kit import WorkflowKit
from browser_workflows.operations.common import Operation, OperationOutcome, ok, refused
from browser_workflows.presenting.terminal import TerminalConsole

from .kit import CashyKit
from .mediator import CashyAgent
from .models import CashyReply, Intent, IntentKind

KitFactory = Callable[[ int ], CashyKit ]


class CashyRuntime:
    """One CashyKit (and its agent) per port, made when first asked for."""

    def __init__( self, factory: KitFactory ) -> None:
        self._factory = factory
        self._kits: dict[ int, CashyKit ] = {}
        self._agents: dict[ int, CashyAgent ] = {}

    def kit( self, args: dict[ str, Any ]) -> CashyKit:
        port = int( args.get( "port" ) or 9222 )
        if port not in self._kits:
            self._kits[ port ] = self._factory( port )
        return self._kits[ port ]

    def agent( self, args: dict[ str, Any ]) -> CashyAgent:
        port = int( args.get( "port" ) or 9222 )
        if port not in self._agents:
            self._agents[ port ] = self.kit( args ).agent()
        return self._agents[ port ]


def _outcome( reply: CashyReply ) -> OperationOutcome:
    data = { "state": reply.state, "events": [ e.model_dump() for e in reply.events ]}
    return ok( reply.text, **data ) if reply.ok else refused( reply.text, **data )


def _session( args: dict[ str, Any ]) -> str:
    return str( args.get( "session" ) or "default" )


def _actor( args: dict[ str, Any ]) -> str:
    return str( args.get( "actor" ) or "user" )


class CashyTurn( Operation ):
    """One sentence, exactly as a person would say it to Cashy."""
    name        = "cashy_turn"
    description = "Say one thing to Cashy (e.g. \"open Chase\", \"download my September 2025 statement\")"
    performer   = "cashy"

    def __init__( self, runtime: CashyRuntime ) -> None:
        self._runtime = runtime

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        text = str( args.get( "text" ) or "" ).strip()
        if not text:
            return refused( "say something: --text \"open Chase\"" )
        return _outcome( self._runtime.agent( args ).turn( text, _actor( args ), _session( args )))


class CashyIntent( Operation ):
    """One step without the sentence: the flags fill the intent directly,
    and the session's State decides whether it may run now."""
    performer = "cashy"

    def __init__( self, runtime: CashyRuntime, kind: IntentKind, description: str, fields: tuple[ str, ... ] = ()) -> None:
        self._runtime    = runtime
        self._kind       = kind
        self._fields     = fields
        self.name        = f"cashy_{ kind }"
        self.description = description

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        values: dict[ str, Any ] = { f: args[ f ] for f in self._fields if f in args and args[ f ] not in ( None, "" )}
        if "period" in values:
            period = self._runtime.kit( args ).workflows.period_parser().parse( str( values[ "period" ]))
            if period is None:
                return refused( f"not a month and year: { values[ 'period' ]!r}" )
            values[ "period" ] = period
        if "bank" in values:
            bank = self._runtime.kit( args ).banks().find( str( values[ "bank" ]))
            if bank is None:
                return refused( f"I don't know a bank called { values[ 'bank' ]!r}" )
            values[ "bank" ] = bank.key
        intent = Intent( kind=self._kind, text=str( args.get( "text" ) or "" ), **values )
        return _outcome( self._runtime.agent( args ).perform([ intent ], _actor( args ), _session( args )))


class SearchMemories( Operation ):
    name        = "cashy_search_memories"
    description = "What Cashy remembers learning, by words and optionally bank"
    performer   = "cashy"

    def __init__( self, runtime: CashyRuntime ) -> None:
        self._runtime = runtime

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        memory = self._runtime.kit( args ).memory()
        if not memory.available:
            return refused( "LanceDB isn't installed here, so there are no learned memories to search" )
        found = memory.search( str( args.get( "text" ) or "statement" ), str( args.get( "bank" ) or "" ))
        if not found:
            return ok( "Nothing learned matches that yet.", memories=[])
        lines = [ f"{ m.bank } / { m.goal } v{ m.version } ({ m.playback }): { m.summary }" for m in found ]
        return ok( "\n".join( lines ), memories=[ m.model_dump() for m in found ])


class ListWorkflows( Operation ):
    name        = "cashy_list_workflows"
    description = "Every version of every banking workflow Cashy learned, and whether a live run proved it"
    performer   = "cashy"

    def __init__( self, runtime: CashyRuntime ) -> None:
        self._runtime = runtime

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        versions = self._runtime.kit( args ).registry().versions( str( args.get( "bank" ) or "" ),
                                                                 str( args.get( "goal" ) or "" ))
        if not versions:
            return ok( "No learned banking workflows yet.", versions=[])
        lines = [ f"{ v.bank:<14} { v.goal:<20} v{ v.version:<3} { v.status:<9} { v.playback }"
                  + ( f"  needs { ', '.join( v.needs )}" if v.needs else "" ) for v in versions ]
        return ok( "\n".join( lines ), versions=[ v.model_dump() for v in versions ])


class DownloadHistory( Operation ):
    name        = "cashy_download_history"
    description = "The statements Cashy downloaded: bank, masked account, month, file"
    performer   = "cashy"

    def __init__( self, runtime: CashyRuntime ) -> None:
        self._runtime = runtime

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        rows = self._runtime.kit( args ).ledger().recent( int( args.get( "limit" ) or 20 ))
        if not rows:
            return ok( "Nothing downloaded yet.", downloads=[])
        lines = [ f"{ r.at[ :10 ]}  { r.bank:<12} { r.account or '-':<7} { r.period:<8} { r.path }" for r in rows ]
        return ok( "\n".join( lines ), downloads=[ r.model_dump() for r in rows ])


#: The fine-grained steps, each one Cashy command: ( kind, description, flags it reads ).
INTENT_STEPS: list[ tuple[ IntentKind, str, tuple[ str, ... ]]] = [
    ( "open_bank",        "Open a bank's login page (or run its login ADW) and wait for you to sign in", ( "bank", )),
    ( "logged_in",        "Tell Cashy you've signed in; she checks the page before believing it", ()),
    ( "list_options",     "Read the current page's links and buttons out of the DOM", ()),
    ( "click",            "Click the element whose words match --target", ( "target", )),
    ( "click_number",     "Click option N from the last list Cashy read out", ( "number", )),
    ( "go_back",          "Go back one page and forget that step", ()),
    ( "go_to",            "Reach a destination (statements, transfer_activity, ...) by ADW or by exploring", ( "goal", "bank" )),
    ( "get_statement",    "Download one month's statement", ( "bank", "period", "account", "again" )),
    ( "find_transaction", "Open transfer activity for a payee, after checking it against your records", ( "target", "account", "bank" )),
    ( "remember",         "Save how Cashy got here as a new candidate workflow version", ()),
    ( "status",           "Where the session is and what it is waiting for", ()),
    ( "stop",             "Stop here; drop any open question", ()),
    ( "pause",            "Pause the session", ()),
    ( "resume",           "Resume the paused or interrupted task", ()),
]


def cashy_operations( kit: WorkflowKit, console: TerminalConsole,
                      factory: KitFactory | None = None ) -> dict[ str, Any ]:
    """The run_browser_operation catalog. The WorkflowKit it is handed is not
    used: Cashy builds her own, pinned to the conversation's tab."""
    runtime = CashyRuntime( factory or ( lambda port: CashyKit( port=port )))
    found: list[ Any ] = [ CashyTurn( runtime ), SearchMemories( runtime ), ListWorkflows( runtime ), DownloadHistory( runtime )]
    found += [ CashyIntent( runtime, kind, text, fields ) for kind, text, fields in INTENT_STEPS ]
    return { op.name: op for op in found }
