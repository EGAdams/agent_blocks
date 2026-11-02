"""The shared harness under every `adw_liz_<op>.py` -- Liz's fine-grained city-tax ADWs.

One ADW, one step of the Grand Rapids return. Each `adw_liz_*.py` declares
its `LizStep` (name, the workflow state it works in, what it reads and
writes) and its phases; this module does everything they have in common, the
way `browser_workflows/browser_workflow_adw.py` does for the browser ADWs:

* an SSSF session per run (`SessionRunFactory`), so every step and every
  phase shows in the existing ADW logs and monitor;
* the workflow State machine: the step's state on entry, a checkpoint on
  success, FAILED (naming this step, so only it reruns) on an exception;
* a checkpointed step is skipped unless `--force`, so a resumed workflow
  never re-downloads forms or re-reads documents it already has;
* the one line Liz repeats, written to `--message-out` (or stdout).

It is also the composition root for the concretes those phases need -- the
only place that names `JsonWorkpapers`, the ADW Generated link index,
Scala's OCR reader, the company to-do store, the read-only books.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path( __file__ ).resolve().parent
for _path in ( _HERE, _HERE.parent / "lancedb_memory" ):
    if str( _path ) not in sys.path:
        sys.path.insert( 0, str( _path ))

from sssf_roles import ( ConsoleLifecycleLog, ILifecycleLog, IPhase, IPhaseHandle, LifecycleLogged,  # noqa: E402
                         ManualContextPhaseExecutor, PrintLifecycleLog, SessionRunFactory, SssfConfigLoader )

from adw_modules.data_types import PhaseParams  # noqa: E402

from city_tax.state import JsonWorkflowStore, LizWorkflow, WorkflowState  # noqa: E402
from city_tax.store import JsonWorkpapers, liz_home, private_dir, write_private  # noqa: E402

REQUIRED_AGENTS: list[ str ] = []          # no model in any Liz step today
WEB_PAGES = Path( os.environ.get( "ADW_GENERATED_DIR", str( _HERE / "adw_data" / "web_pages" )))


@dataclass( frozen=True )
class LizStep:
    name:        str
    state:       WorkflowState
    description: str
    reads:       tuple[ str, ... ] = ()      # workpapers this step needs
    writes:      tuple[ str, ... ] = ()      # workpapers this step produces
    checkpointed: bool = True                # False: always runs (status, asks, waits)
    suffix_arg:  str = ""                    # e.g. "year": checkpoint as read_tax_pdf_2025

    def key( self, args: argparse.Namespace ) -> str:
        return f"{ self.name }_{ getattr( args, self.suffix_arg )}" if self.suffix_arg else self.name


@dataclass
class Kit:
    """What a phase may use. Built once per run, here and nowhere else."""
    args:       argparse.Namespace
    papers:     JsonWorkpapers
    flow:       LizWorkflow
    home:       Path
    store:      Any = None
    line:       str = ""
    outputs:    list[ str ] = field( default_factory=list )
    _cache:     dict[ str, Any ] = field( default_factory=dict )

    def need( self, name: str ) -> Any:
        data = self.papers.load( name )
        if data is None:
            raise RuntimeError( f"Liz has no '{ name }' workpaper yet -- run the step that writes it first." )
        return data

    def save( self, name: str, data: object ) -> Path:
        path = self.papers.save( name, data )
        self.outputs.append( str( path ))
        return path

    # -- concretes, built on first use -----------------------------------------
    def ocr( self ):
        if "ocr" not in self._cache:
            from scala.ocr import TesseractOcr
            self._cache[ "ocr" ] = TesseractOcr()
        return self._cache[ "ocr" ]

    def pdf_reader( self ):
        from city_tax.pdf_text import PopplerTesseractReader
        return PopplerTesseractReader()

    def todos( self ):
        from company_todos.store import JsonlTodoStore
        return JsonlTodoStore()

    def scan_desk( self ):
        from city_tax.scans import ScanDesk
        from scala.ledger import JsonlScalaLedger
        return ScanDesk( self.todos(), JsonlScalaLedger(), self.ocr())

    def publisher( self ) -> "AdwGeneratedPublisher":
        return AdwGeneratedPublisher( WEB_PAGES, PrintLifecycleLog())


class FunctionPhase( LifecycleLogged, IPhase ):
    """One named phase whose behaviour is a function of the kit."""

    def __init__( self, step: LizStep, name: str, description: str, act: Callable[[ Kit, IPhaseHandle ], Any ],
                  kit: Kit, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._step, self._name, self._description, self._act, self._kit = step, name, description, act, kit

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="liz", description=self._description )

    def act( self, handle: IPhaseHandle ) -> Any:
        return self._act( self._kit, handle )


def _load_by_location( module_name: str, filename: str ):
    cached = sys.modules.get( module_name )
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location( module_name, _HERE / filename )
    if spec is None or spec.loader is None:
        raise ImportError( f"Could not load { filename }" )
    module = importlib.util.module_from_spec( spec )
    sys.modules[ module_name ] = module
    spec.loader.exec_module( module )
    return module


class AdwGeneratedPublisher( LifecycleLogged ):
    """IReportPublisher: write the page beside the others, then the existing link index lists it."""

    def __init__( self, folder: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._folder = folder

    def publish( self, html: str, stem: str, summary: str ) -> str:
        from datetime import datetime
        index = _load_by_location( "adw_interface_category_web_page", "adw_interface_category_web_page.py" )
        self._folder.mkdir( parents=True, exist_ok=True )
        now = datetime.now()
        page = self._folder / f"{ stem }_{ now:%B}_{ now.day }_{ now:%H_%M_%S}.html"
        page.write_text( html, encoding="utf-8" )
        index.HtmlLinkIndex( PrintLifecycleLog()).publish( page, summary )
        return page.name


def read_only_books():
    """The live-books ADW's own read-only stack, by location -- never a second copy."""
    live = _load_by_location( "adw_interface_live_finance", "adw_interface_live_finance.py" )
    log = PrintLifecycleLog()
    books = live.ReadOnlyBooks( live.PyMySQLLiveBooks( live.EnvFileCredentials( live.DEFAULT_ENV_FILE, log ), log ), log )
    return books


Phase = tuple[ str, str, Callable[[ Kit, IPhaseHandle ], Any ]]


def run_step( step: LizStep, doc: str, phases: Callable[[ Kit ], list[ Phase ]],
              add_args: Callable[[ argparse.ArgumentParser ], None] | None = None,
              argv: list[ str ] | None = None ) -> int:
    parser = argparse.ArgumentParser( description=doc, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "prompt", nargs="?", default=step.description )
    parser.add_argument( "--message-out", default="" )
    parser.add_argument( "--force", action="store_true", help="run even if this step has a checkpoint" )
    parser.add_argument( "--config", default=str( _HERE / "adw_sssf_config" / "sssf.config.yaml" ))
    parser.add_argument( "--adw-id", default=None )
    if add_args:
        add_args( parser )
    args = parser.parse_args( sys.argv[ 1: ] if argv is None else argv )

    home = private_dir( liz_home())
    store = JsonWorkflowStore( home )
    flow = store.load()
    kit = Kit( args=args, papers=JsonWorkpapers( home ), flow=flow, home=home, store=store )

    def say( line: str ) -> None:
        if args.message_out:
            Path( args.message_out ).write_text( line + "\n" )
        else:
            print( line )

    name = step.key( args )
    if step.checkpointed and flow.done( name ) and not args.force:
        cp = flow.checkpoints[ name ]
        say( f"Already done ({ cp.at:%b %d %H:%M}): { cp.summary or step.description }." )
        return 0

    boot: ILifecycleLog = PrintLifecycleLog()
    loader = SssfConfigLoader( boot )
    cfg = loader.load( args.config )
    loader.validate( cfg, REQUIRED_AGENTS )
    run = SessionRunFactory( boot ).create( cfg, args.adw_id )
    log: ILifecycleLog = ConsoleLifecycleLog( run )
    executor = ManualContextPhaseExecutor( boot )

    if flow.state is WorkflowState.PAUSED and step.checkpointed:
        flow.resume()
    try:
        if step.state is not flow.state and step.checkpointed:
            flow.move( step.state, name )
            store.save( flow )
        for phase_name, description, act in phases( kit ):
            executor.execute( run, FunctionPhase( step, phase_name, description, act, kit, log ))
    except BaseException as error:  # noqa: BLE001 -- recorded and spoken; the run log has the traceback
        flow = kit.flow
        try:
            flow.fail( name, f"{ type( error ).__name__ }: { error }" )
        except ValueError:
            pass
        store.save( flow )
        say( f"Liz's step '{ name }' failed: { error }" )
        run.finish( accepted=False, reason=str( error )[ :300 ])
        return 1
    if step.checkpointed:
        kit.flow.checkpoint( name, kit.outputs, kit.line[ :200 ])
    store.save( kit.flow )
    say( kit.line or f"{ step.description }: done." )
    return run.finish( accepted=True, reason=kit.line[ :300 ])


def write_text_private( path: Path, text: str ) -> Path:
    return write_private( path, text )
