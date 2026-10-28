"""The shared harness behind every `adw_op_*.py`: one post-office operation as one ADW run.

Single responsibility: turn an `agent_comms` operation (an IOperation) into
an SSSF run -- parse its flags, open a session, execute it as a lifecycle
phase through `ManualContextPhaseExecutor`, write the operator's line to
`--message-out`, and finish the run. A composite operation (an
OperationSequence) runs each of its steps as its own phase, so a composed
workflow is observable step by step in `just obs`.

Each `adw_op_<name>.py` is then a few lines: its docstring, its flags, and
`run_operation( "<name>", ...)`. No ADW carries a runtime of its own; they all
share this one, the SSSF session machinery, and the post office's mailboxes.

Run these under lancedb_memory's venv (it has pydantic, the SSSF modules'
yaml/rich/dotenv, and the Claude Agent SDK a worker needs):

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_hello_world.py
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))
sys.path.insert( 0, str( _HERE.parent / "lancedb_memory" ))

from sssf_roles import ( IPhase, IPhaseHandle, ILifecycleLog, LifecycleLogged, ManualContextPhaseExecutor,
                         PrintLifecycleLog, SessionRunFactory, SssfConfigLoader )

from adw_modules.data_types import PhaseParams
from agent_comms.factory import build_post_office
from agent_comms.operations.base import IOperation, OperationOutcome
from agent_comms.operations.catalog import operations


@dataclass( frozen=True )
class Flag:
    """One command-line flag an operation takes, mapped to one argument key."""
    key:      str
    help:     str
    required: bool = False
    kind:     str  = "str"     # "str", "float", "int", "bool", "json"
    option:   str  = ""        # the CLI spelling, when "--<key>" would clash with the harness's own flags


class OperationPhase( LifecycleLogged, IPhase ):
    """One operation as one lifecycle phase. Its owner is the operation's performer."""

    def __init__( self, operation: IOperation, args: dict[ str, Any ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._operation = operation
        self._args      = args

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._operation.name, kind="code", owner=self._operation.performer,
                            description=self._operation.description )

    def act( self, handle: IPhaseHandle ) -> OperationOutcome:
        handle.log( args=_loggable( self._args ))
        outcome = self._operation.run( self._args )
        handle.log( ok=outcome.ok, line=outcome.line, data=_loggable( outcome.data ))
        return outcome


def run_operation( name: str, description: str, argv: list[ str ], flags: list[ Flag ]) -> int:
    parsed = _parser( description, flags ).parse_args( argv )
    args   = { f.key: getattr( parsed, f.key ) for f in flags if getattr( parsed, f.key ) is not None }
    log: ILifecycleLog = PrintLifecycleLog()
    loader   = SssfConfigLoader( log )
    cfg      = loader.load( parsed.config )
    loader.validate( cfg, [])
    run      = SessionRunFactory( log ).create( cfg, parsed.adw_id )
    executor = ManualContextPhaseExecutor( log )
    kit      = build_post_office()
    op       = operations( kit )[ name ]

    def as_phase( step: IOperation, step_args: dict[ str, Any ]) -> OperationOutcome:
        return executor.execute( run, OperationPhase( step, step_args, log ))

    steps = getattr( op, "steps", None )
    outcome = op.run( args, step_runner=as_phase ) if steps else as_phase( op, args )

    if parsed.message_out:
        Path( parsed.message_out ).write_text( outcome.line + "\n" )
    if parsed.json_out:
        Path( parsed.json_out ).write_text( outcome.model_dump_json( indent=2 ))
    print( outcome.line )
    return run.finish( accepted=outcome.ok, reason=outcome.line )


def _parser( description: str, flags: list[ Flag ]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser( description=description, formatter_class=argparse.RawDescriptionHelpFormatter )
    for f in flags:
        option = f.option or "--" + f.key.replace( "_", "-" )
        if f.kind == "bool":
            parser.add_argument( option, dest=f.key, action="store_true", default=None, help=f.help )
        else:
            parser.add_argument( option, dest=f.key, required=f.required, help=f.help, type=_TYPES[ f.kind ])
    parser.add_argument( "--message-out", default="", help="write the operator's one line here" )
    parser.add_argument( "--json-out", default="", help="write the whole outcome here as JSON" )
    parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
    parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
    return parser


_TYPES = { "str": str, "float": float, "int": int, "json": json.loads }


def _loggable( value: dict[ str, Any ]) -> dict[ str, Any ]:
    text = json.dumps( value, default=str )
    return json.loads( text ) if len( text ) < 4000 else { "truncated": text[ :4000 ]}
