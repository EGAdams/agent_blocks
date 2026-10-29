"""The shared harness behind every `adw_browser_*.py`: one browser-workflow
operation as one ADW run.

The same shape as `post_office_adw.py` -- parse the operation's flags, open
an SSSF session, run the operation as a lifecycle phase through
`ManualContextPhaseExecutor`, write its one line to `--message-out`, finish
the run -- over `browser_workflows.operations.catalog` instead of the post
office's. A composite (`execute_saved_playback`) runs each step as its own
phase, so `just obs` shows load -> validate -> execute.

It differs in one way that matters: an operation may name argument keys in
`redact` (a typed value, say), and those are logged as "(redacted)", never
as what was typed. Secrets never arrive here at all -- playback asks the
person for them with getpass -- but a field's value can still be private.

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_list_recordings.py
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))
sys.path.insert( 1, str( _HERE.parent / "lancedb_memory" ))      # agent_comms.operations.base only

from sssf_roles import ( IPhase, IPhaseHandle, ILifecycleLog, LifecycleLogged, ManualContextPhaseExecutor,
                         PrintLifecycleLog, SessionRunFactory, SssfConfigLoader )

from adw_modules.data_types import PhaseParams
from agent_comms.operations.base import IOperation, OperationOutcome

from browser_workflows.kit import TRANSPORTS, WorkflowKit
from browser_workflows.operations.catalog import operations
from browser_workflows.presenting.terminal import TerminalConsole

__all__ = [ "Flag", "run_browser_operation", "ENDPOINT_FLAGS", "LOCATOR_FLAGS", "GATE_FLAGS" ]

REDACTED = "(redacted)"


@dataclass( frozen=True )
class Flag:
    """One command-line flag an operation takes, mapped to one argument key
    (post_office_adw.Flag's shape; not imported, since that module loads the
    whole post office)."""
    key:      str
    help:     str
    required: bool = False
    kind:     str  = "str"     # "str", "float", "int", "bool", "json"
    option:   str  = ""

ENDPOINT_FLAGS = [ Flag( "port", "the debug Chrome's DevTools port (default 9222)", kind="int" )]
LOCATOR_FLAGS  = [
    Flag( "css", "a CSS selector for the element" ),
    Flag( "label", "the element's label or aria-label" ),
    Flag( "text", "the element's visible text" ),
    Flag( "locators", "a JSON list of locator candidates, best first", kind="json" ),
]
GATE_FLAGS = [
    Flag( "interactive", "a person here approves consequential steps and types secrets", kind="bool" ),
    Flag( "approve", "pre-approve these step numbers, e.g. 4,7 (secrets still block)" ),
]


class BrowserOperationPhase( LifecycleLogged, IPhase ):
    """One operation as one lifecycle phase, its `redact` keys hidden in the log."""

    def __init__( self, operation: IOperation, args: dict[ str, Any ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._operation = operation
        self._args      = args

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._operation.name, kind="code", owner=self._operation.performer,
                            description=self._operation.description )

    def act( self, handle: IPhaseHandle ) -> OperationOutcome:
        hidden = tuple( getattr( self._operation, "redact", ()))
        handle.log( args=_loggable( self._args, hidden ))
        outcome = self._operation.run( self._args )
        handle.log( ok=outcome.ok, line=outcome.line, data=_loggable( outcome.data, hidden ))
        return outcome


#: Builds the operation table a script runs from: the kit and console it is given.
CatalogFactory = Callable[[ WorkflowKit, TerminalConsole ], dict[ str, IOperation ]]


def run_browser_operation( name: str, description: str, argv: list[ str ], flags: list[ Flag ],
                           catalog: CatalogFactory = operations, default_transport: str = "selenium" ) -> int:
    parsed = _parser( description, flags, default_transport ).parse_args( argv )
    args   = { f.key: getattr( parsed, f.key ) for f in flags if getattr( parsed, f.key ) is not None }
    log: ILifecycleLog = PrintLifecycleLog()
    loader   = SssfConfigLoader( log )
    cfg      = loader.load( parsed.config )
    loader.validate( cfg, [])
    run      = SessionRunFactory( log ).create( cfg, parsed.adw_id )
    executor = ManualContextPhaseExecutor( log )
    op       = catalog( WorkflowKit( transport=parsed.transport ), TerminalConsole())[ name ]

    def as_phase( step: IOperation, step_args: dict[ str, Any ]) -> OperationOutcome:
        return executor.execute( run, BrowserOperationPhase( step, step_args, log ))

    steps   = getattr( op, "steps", None )
    outcome = op.run( args, step_runner=as_phase ) if steps else as_phase( op, args )

    if parsed.message_out:
        Path( parsed.message_out ).write_text( outcome.line + "\n" )
    if parsed.json_out:
        Path( parsed.json_out ).write_text( json.dumps( _scrub( outcome.model_dump(), set( _hidden( op ))), indent=2 ))
    print( outcome.line )
    if parsed.verdict:                                    # agent_comms' AdwSubprocessExecutor reads the last one
        print( f"{ 'DONE' if outcome.ok else 'REFUSED' }: { ' | '.join( outcome.line.splitlines()) or name }" )
    return run.finish( accepted=outcome.ok, reason=outcome.line.splitlines()[ 0 ] if outcome.line else "" )


def _hidden( op: IOperation ) -> tuple[ str, ... ]:
    keys = list( getattr( op, "redact", ()))
    for step in getattr( op, "steps", None ) or []:
        keys += getattr( step, "redact", ())
    return tuple( keys )


_TYPES = { "str": str, "float": float, "int": int, "json": json.loads }


def _parser( description: str, flags: list[ Flag ], default_transport: str ) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser( description=description, formatter_class=argparse.RawDescriptionHelpFormatter )
    for f in flags:
        option = f.option or "--" + f.key.replace( "_", "-" )
        if f.kind == "bool":
            parser.add_argument( option, dest=f.key, action="store_true", default=None, help=f.help )
        else:
            parser.add_argument( option, dest=f.key, required=f.required, help=f.help, type=_TYPES[ f.kind ])
    parser.add_argument( "--transport", choices=TRANSPORTS, default=default_transport,
                         help="attach over selenium (chromedriver) or cdp (DevTools protocol, no driver)" )
    parser.add_argument( "--message-out", default="", help="write the operator's one line here" )
    parser.add_argument( "--json-out", default="", help="write the whole outcome here as JSON" )
    parser.add_argument( "--verdict", action="store_true", help="end on a DONE:/REFUSED: line, for the post office" )
    parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
    parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
    return parser


def _loggable( value: Any, hidden: tuple[ str, ... ]) -> Any:
    clean = _scrub( value, set( hidden ))
    text  = json.dumps( clean, default=str )
    return clean if len( text ) < 4000 else { "truncated": text[ :4000 ]}


def _scrub( value: Any, hidden: set[ str ]) -> Any:
    if isinstance( value, dict ):
        return { k: ( REDACTED if k in hidden and v not in ( None, "" ) else _scrub( v, hidden )) for k, v in value.items()}
    if isinstance( value, list ):
        return [ _scrub( v, hidden ) for v in value ]
    return value
