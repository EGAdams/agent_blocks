#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Interface Company Todos -- add to, read, and check off the company to-do list.

Toyota's `company_todos` route runs this as a subprocess and repeats the one
line it writes, with no model in the path, the same posture as live_books:
the list's contents are records, and a model in between is a chance for an
item to be invented, renamed or "checked off" that never was.

    read_request -> apply_change -> report

`--request` is the user's sentence, read by `company_todos.requests` (pattern
rules, no model). `--action` skips the reading for scripts and people:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_interface_company_todos.py \\
        "why" --request "add renew the church insurance to the to-do list" [--by toyota] \\
        [--action list|add|done --text "..." --number 3] \\
        [--message-out /tmp/line.txt] [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

The list lives in `~/company_todos/todos.jsonl` (COMPANY_TODOS_HOME overrides),
outside the repository; it is append-only, so it is also the history.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))
sys.path.insert( 0, str( _HERE.parent / "lancedb_memory" ))

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import utils
from adw_modules.data_types import PhaseParams

from company_todos.desk import TodoDesk
from company_todos.interfaces import ITodoStore
from company_todos.models import TodoAction, TodoCommand
from company_todos.requests import read_request
from company_todos.store import JsonlTodoStore

REQUIRED_AGENTS: list[ str ] = []   # no model anywhere in this workflow


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class TodoRequest:
    prompt:      str
    request:     str
    action:      str
    text:        str
    number:      int | None
    by:          str
    message_out: str
    config:      str
    adw_id:      str | None


@dataclass
class TodoRun:
    """What the phases hand each other."""
    request: TodoRequest
    command: TodoCommand | None = None
    line:    str = ""
    notes:   list[ str ] = field( default_factory=list )


# -- Phases ------------------------------------------------------------------

class ReadRequestPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: TodoRun, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="read_request", kind="code", owner="company_todos",
                            description="Read the request as add / list / done, without a model" )

    def act( self, handle: IPhaseHandle ) -> str:
        r = self._state.request
        if r.action:
            command = TodoCommand( action=TodoAction( r.action ), text=r.text, number=r.number )
        else:
            command = read_request( r.request )
        self._state.command = command
        handle.log( action=command.action.value, number=command.number )
        return command.action.value


class ApplyChangePhase( LifecycleLogged, IPhase ):
    def __init__( self, state: TodoRun, store: ITodoStore, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state
        self._store = store

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="apply_change", kind="code", owner="company_todos",
                            description="Add, check off, or read the list; unclear changes nothing" )

    def act( self, handle: IPhaseHandle ) -> str:
        assert self._state.command is not None
        self._state.line = TodoDesk( self._store, self._state.request.by ).handle( self._state.command )
        handle.log( result=self._state.line )
        return self._state.line


class ReportPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: TodoRun, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="company_todos",
                            description="Write the one line the receptionist repeats" )

    def act( self, handle: IPhaseHandle ) -> str:
        out = self._state.request.message_out
        if out:
            Path( out ).write_text( self._state.line + "\n" )
        else:
            print( self._state.line )
        return out or "stdout"


# -- Argument parsing --------------------------------------------------------

class ArgparseTodoArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> TodoRequest:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="company to-do list" )
        parser.add_argument( "--request", default="", help="the user's sentence" )
        parser.add_argument( "--action", default="", choices=[ "", "add", "list", "done" ])
        parser.add_argument( "--text", default="" )
        parser.add_argument( "--number", type=int, default=None )
        parser.add_argument( "--by", default="toyota" )
        parser.add_argument( "--message-out", default="" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None )
        parsed = parser.parse_args( argv )
        if not parsed.request and not parsed.action:
            parser.error( "give --request (a sentence) or --action" )
        if parsed.action == "add" and not parsed.text:
            parser.error( "--action add needs --text" )
        if parsed.action == "done" and not ( parsed.text or parsed.number ):
            parser.error( "--action done needs --number or --text" )
        return TodoRequest( prompt=utils.resolve_prompt( parsed.prompt ), request=parsed.request,
                            action=parsed.action, text=parsed.text, number=parsed.number,
                            by=parsed.by, message_out=parsed.message_out, config=parsed.config,
                            adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class TodoApplication( LifecycleLogged, IApplication ):
    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  store:           ITodoStore,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._store           = store

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        state = TodoRun( request=request )
        for phase in ( ReadRequestPhase( state, session_log ),
                       ApplyChangePhase( state, self._store, session_log ),
                       ReportPhase( state, session_log )):
            self._phase_executor.execute( run, phase )
        accepted = state.command is not None and state.command.action is not TodoAction.UNCLEAR
        return run.finish( accepted=accepted, reason=state.line )


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    return TodoApplication(
        argument_parser = ArgparseTodoArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(              bootstrap_log          ),
        session_factory = SessionRunFactory(             bootstrap_log          ),
        phase_executor  = ManualContextPhaseExecutor(    bootstrap_log          ),
        store           = JsonlTodoStore(),
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))
