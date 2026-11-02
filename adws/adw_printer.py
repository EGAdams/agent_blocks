#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "pillow", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Printer -- print one approved job on EG's local printers, or ask about jobs.

The ChatGPT bridge worker's `PrinterAgent` runs this, one subcommand per bridge
operation. It never takes a file path or a shell command from the request: text
travels inline, a file is named by its print-outbox id, and a printer by the id
discovery gave it.

    print    --job-id ID   request JSON on stdin; prints at most once per job id
    status   --job-id ID   reads the registry and the Windows queue; never prints
    cancel   --job-id ID   stops a job not yet sent, or removes it from the queue
    recent                 the latest jobs, from the registry only
    discover               printers, the default, outbox documents, recent jobs

    print phases:  ReceivePrintRequest -> ValidateDocument -> ResolvePrinter
                   -> RenderDocument -> SubmitPrintJob -> MonitorPrintJob -> ReportResult

The logic is `printer_agent` (`PrintWorkflow`, `JobControl`); `PrintWorkflow.run()`
is the same print without the ADW. SSSF logging goes to stderr. The last line
on stdout is always `PRINTER_RESULT {json}`: a report, or {"error": "..."}.

    echo '{"kind": "test_page"}' | uv run adws/adw_printer.py print --job-id t_0123456789abcdef01234567
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

_HERE = Path( __file__ ).resolve().parent
for _path in ( _HERE, _HERE.parent / "lancedb_memory" ):
    if str( _path ) not in sys.path:
        sys.path.insert( 0, str( _path ))

from sssf_roles import ( ConsoleLifecycleLog, ILifecycleLog, IPhase, IPhaseHandle, LifecycleLogged,  # noqa: E402
                         ManualContextPhaseExecutor, PrintLifecycleLog, SessionRunFactory, SssfConfigLoader )

from adw_modules.data_types import PhaseParams  # noqa: E402

from pydantic import BaseModel, ConfigDict  # noqa: E402

from printer_agent.errors import PrinterError  # noqa: E402
from printer_agent.job_control import JobControl  # noqa: E402
from printer_agent.models import JOB_ID, PrintLimits, PrintReport  # noqa: E402
from printer_agent.reports import report as report_of  # noqa: E402
from printer_agent.retention import sweep  # noqa: E402
from printer_agent.service import PrintWorkflow  # noqa: E402

MARKER      = "PRINTER_RESULT "
DATA_DIR    = _HERE / "adw_data" / "printer"
OUTBOX      = Path.home() / "print_outbox"
SPOOL_ROOT  = Path( "/mnt/c/Users/NewUser/AppData/Local/startrek_printer/spool" )
RECENT      = 10


class PrinterApplication( BaseModel ):
    """What `build_application()` assembles: the two services and the rules they share."""

    model_config = ConfigDict( arbitrary_types_allowed=True, frozen=True )

    workflow:   PrintWorkflow
    control:    JobControl
    limits:     PrintLimits
    spool_root: Path


class PrinterPhase( LifecycleLogged, IPhase ):
    """One SSSF phase that calls one PrintWorkflow step."""

    def __init__( self, name: str, description: str, act: Callable[[], Any ], logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._name, self._description, self._act = name, description, act

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="chatgpt-bridge", description=self._description )

    def act( self, handle: IPhaseHandle ) -> Any:
        return self._act()


def build_application( data_dir: Path = DATA_DIR, outbox: Path = OUTBOX, spool_root: Path = SPOOL_ROOT,
                       limits: PrintLimits = PrintLimits()) -> PrinterApplication:
    """The composition root: the only place that names a concrete."""
    from printer_agent.adapters.print_lock import FlockPrintLock
    from printer_agent.adapters.subprocess_runner import SubprocessRunner
    from printer_agent.adapters.windows_spooler import PowerShell, WindowsPrinterDiscovery, WindowsSpoolerBackend
    from printer_agent.catalog import OutboxCatalog
    from printer_agent.registry import SqliteJobRegistry
    from printer_agent.renderers import PdfRenderer, RendererRegistry, TextRenderer

    runner    = SubprocessRunner()
    shell     = PowerShell( runner )
    discovery = WindowsPrinterDiscovery( shell )
    backend   = WindowsSpoolerBackend( shell, spool_root )
    pdf       = PdfRenderer( runner, limits.max_pages, data_dir / "render" )
    renderer  = RendererRegistry().register( "text", TextRenderer( limits.max_pages )).register( "pdf", pdf )
    catalog   = OutboxCatalog( outbox, limits.max_document_bytes, page_count=pdf.page_count )
    registry  = SqliteJobRegistry( data_dir / "jobs.sqlite" )
    workflow  = PrintWorkflow( discovery, renderer, backend, catalog, registry, FlockPrintLock( data_dir / "print.lock" ),
                               SystemClock(), lambda: datetime.now().astimezone(), limits )
    return PrinterApplication( workflow=workflow, control=JobControl( discovery, backend, catalog, registry ),
                               limits=limits, spool_root=spool_root )


class SystemClock:
    def monotonic( self ) -> float:
        return time.monotonic()

    def sleep( self, seconds: float ) -> None:
        time.sleep( seconds )


class PrintRun:
    """The print phases' shared state: each phase reads what the one before it left."""

    def __init__( self, workflow: PrintWorkflow, job_id: str, prompt: str ) -> None:
        self.workflow, self.job_id, self.prompt = workflow, job_id, prompt
        self.request = self.document = self.printer = self.rendered = None
        self.finished: PrintReport | None = None      # set when a step ended the job early

    def phases( self ) -> list[ tuple[ str, str, Callable[[], Any ]]]:
        return [
            ( "receive_print_request", "Register the job once under its task id; parse the request", self.receive ),
            ( "validate_document",     "Check type and size; load inline text or an outbox document", self.validate ),
            ( "resolve_printer",       "Pick the named printer, or the default, from discovery", self.resolve ),
            ( "render_document",       "Lay out US Letter page images for the chosen pages", self.render ),
            ( "submit_print_job",      "Hand the pages to the Windows spooler, once", self.submit ),
            ( "monitor_print_job",     "Read the Windows queue until the job leaves it or time runs out", self.monitor ),
            ( "report_result",         "Report the job as the registry and spooler see it", self.report ),
        ]

    def _refusing( self, step: Callable[[], Any ]) -> Any:
        try:
            return step()
        except PrinterError as exc:
            self.finished = report_of( self.workflow.refuse( self.job_id, exc ))
            return self.finished.state.value

    def receive( self ) -> str:
        record, self.request = self.workflow.receive( self.job_id, self.prompt )
        if self.request is None:
            self.finished = report_of( record )
        return record.state.value

    def validate( self ) -> Any:
        if self.finished is None:
            return self._refusing( lambda: setattr( self, "document", self.workflow.validate( self.job_id, self.request )))
        return "skipped"

    def resolve( self ) -> Any:
        if self.finished is None:
            return self._refusing( lambda: setattr( self, "printer", self.workflow.resolve_printer( self.job_id, self.request )))
        return "skipped"

    def render( self ) -> Any:
        if self.finished is None:
            return self._refusing( lambda: setattr( self, "rendered", self.workflow.render( self.job_id, self.document,
                                                                                            self.request )))
        return "skipped"

    def submit( self ) -> str:
        if self.finished is not None:
            return "skipped"
        return self.workflow.submit( self.job_id, self.printer, self.document, self.rendered, self.request ).state.value

    def monitor( self ) -> str:
        if self.finished is not None:
            return "skipped"
        return self.workflow.monitor( self.job_id, self.printer ).state.value

    def report( self ) -> str:
        if self.finished is None:
            self.finished = self.workflow.report( self.job_id )
        return self.finished.state.value


def run_print( app: PrinterApplication, job_id: str, prompt: str, config: str, adw_id: str | None ) -> dict[ str, Any ]:
    boot: ILifecycleLog = PrintLifecycleLog()
    loader = SssfConfigLoader( boot )
    cfg    = loader.load( config )
    loader.validate( cfg, [])
    session  = SessionRunFactory( boot ).create( cfg, adw_id )
    log      = ConsoleLifecycleLog( session )
    executor = ManualContextPhaseExecutor( boot )
    sweep( app.spool_root, app.limits.retention_hours * 3600.0, time.time())
    job = PrintRun( app.workflow, job_id, prompt )
    app.workflow.acquire()
    try:
        for name, description, act in job.phases():
            executor.execute( session, PrinterPhase( name, description, act, log ))
    finally:
        app.workflow.release()
    result = job.finished
    assert result is not None
    session.finish( accepted=result.state.value in { "submitted_to_spooler", "completed_per_spooler" },
                    reason=f"{ result.state.value }: { result.detail }"[ :300 ])
    return { "job": result.model_dump( mode="json" )}


def answer( args: argparse.Namespace, app: PrinterApplication, stdin: Callable[[], str ]) -> dict[ str, Any ]:
    if args.command in { "print", "status", "cancel" } and not JOB_ID.fullmatch( args.job_id or "" ):
        return { "error": "a valid --job-id is required" }
    if args.command == "print":
        prompt = stdin()
        if len( prompt ) > 8000:
            return { "error": "the print request is longer than 8000 characters" }
        return run_print( app, args.job_id, prompt, args.config, args.adw_id )
    if args.command == "status":
        return { "job": app.control.status( args.job_id ).model_dump( mode="json" )}
    if args.command == "cancel":
        return { "job": app.control.cancel( args.job_id ).model_dump( mode="json" )}
    if args.command == "recent":
        return { "recent": [ r.model_dump( mode="json" ) for r in app.control.recent( RECENT )]}
    return app.control.discover( RECENT ).model_dump( mode="json" )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    p.add_argument( "command", choices=[ "print", "status", "cancel", "recent", "discover" ])
    p.add_argument( "--job-id", default="" )
    p.add_argument( "--config", default=str( _HERE / "adw_sssf_config" / "sssf.config.yaml" ))
    p.add_argument( "--adw-id", default=None )
    return p


def main( argv: list[ str ] | None = None, app: PrinterApplication | None = None,
          stdin: Callable[[], str ] = lambda: sys.stdin.read()) -> int:
    args = parser().parse_args( argv )
    # Phase logging goes to stderr, so stdout carries only the one result line.
    with contextlib.redirect_stdout( sys.stderr ):
        try:
            result = answer( args, app or build_application(), stdin )
        except PrinterError as exc:
            result = { "error": str( exc )[ :500 ]}
        except Exception as exc:  # noqa: BLE001 -- the worker needs a line, not a traceback; the type is enough
            result = { "error": f"the printer workflow failed ({ type( exc ).__name__ })" }
    print( MARKER + json.dumps( result, ensure_ascii=False ))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit( main())
