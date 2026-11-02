"""Fake hardware for the printer tests. Nothing here talks to a printer, a spooler or PowerShell."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from printer_agent.adapters.print_lock import FlockPrintLock
from printer_agent.catalog import OutboxCatalog
from printer_agent.errors import SpoolerError
from printer_agent.job_control import JobControl
from printer_agent.models import ( CommandOutput, JobState, PrintDocument, PrinterInfo, PrintJob, PrintLimits, PrintOptions,
                                  PrintResult, PrintStatus, RenderedDocument, RenderedPage )
from printer_agent.registry import SqliteJobRegistry
from printer_agent.service import PrintWorkflow

OFFICEJET = PrinterInfo( printer_id="hpi297bea_hp_officejet_8120e_series", name="HPI297BEA (HP OfficeJet 8120e series)",
                         is_default=True, status="Normal" )
DESKJET   = PrinterInfo( printer_id="hp063e28_hp_deskjet_4100_series", name="HP063E28 (HP DeskJet 4100 series)",
                         status="Normal" )


class FakeDiscovery:
    def __init__( self, printers: list[ PrinterInfo ] | None = None ) -> None:
        self.printers = [ OFFICEJET, DESKJET ] if printers is None else printers
        self.calls = 0

    def list_printers( self ) -> list[ PrinterInfo ]:
        self.calls += 1
        return list( self.printers )


class FakeRenderer:
    def __init__( self, pages: int = 1 ) -> None:
        self.pages = pages
        self.seen: list[ PrintDocument ] = []

    def render( self, document: PrintDocument, options: PrintOptions ) -> RenderedDocument:
        self.seen.append( document )
        return RenderedDocument( pages=[ RenderedPage( png=b"\x89PNG fake" ) for _ in range( self.pages )])


class FakeBackend:
    """Records submissions. `statuses` is what status() answers, in order; the last repeats."""

    def __init__( self, result: PrintResult | None = None, statuses: list[ PrintStatus ] | None = None,
                  raises: Exception | None = None ) -> None:
        self.result   = result or PrintResult( state=JobState.SUBMITTED_TO_SPOOLER, spooler_id=7, detail="in the queue" )
        self.statuses = statuses or [ PrintStatus( state=JobState.COMPLETED_PER_SPOOLER, in_queue=False, detail="released" )]
        self.raises   = raises
        self.submitted: list[ PrintJob ] = []
        self.status_calls = 0
        self.canceled: list[ str ] = []

    def submit( self, job: PrintJob ) -> PrintResult:
        self.submitted.append( job )
        if self.raises is not None:
            raise self.raises
        return self.result

    def status( self, job_id: str, printer: PrinterInfo ) -> PrintStatus:
        self.status_calls += 1
        return self.statuses[ min( self.status_calls - 1, len( self.statuses ) - 1 )]

    def cancel( self, job_id: str, printer: PrinterInfo ) -> PrintResult:
        self.canceled.append( job_id )
        return PrintResult( state=JobState.CANCELED, detail="removed from the Windows queue" )


class FakeClock:
    def __init__( self ) -> None:
        self.t = 0.0

    def monotonic( self ) -> float:
        return self.t

    def sleep( self, seconds: float ) -> None:
        self.t += seconds


class StepNow:
    """A wall clock that moves one second per call, so registry rows keep a stable order."""

    def __init__( self ) -> None:
        self.at = datetime( 2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc )

    def __call__( self ) -> datetime:
        self.at += timedelta( seconds=1 )
        return self.at


class FakeRunner:
    """ICommandRunner: answers by program name, records every argv and stdin."""

    def __init__( self, answers: dict[ str, CommandOutput ] | None = None ) -> None:
        self.answers = answers or {}
        self.calls: list[ tuple[ list[ str ], bytes | None ]] = []

    def run( self, argv: list[ str ], timeout: float, stdin: bytes | None = None ) -> CommandOutput:
        self.calls.append(( argv, stdin ))
        for name, answer in self.answers.items():
            if Path( argv[ 0 ]).name == name:
                return answer
        return CommandOutput( returncode=127 )


class Rig:
    """A workflow wired to fakes and a real registry, catalog and lock in a temp dir."""

    def __init__( self, tmp: Path, backend: FakeBackend | None = None, discovery: FakeDiscovery | None = None,
                  renderer: FakeRenderer | None = None, limits: PrintLimits = PrintLimits()) -> None:
        self.outbox    = tmp / "outbox"
        self.outbox.mkdir()
        self.now       = StepNow()
        self.clock     = FakeClock()
        self.registry  = SqliteJobRegistry( tmp / "jobs.sqlite", now=self.now )
        self.catalog   = OutboxCatalog( self.outbox, limits.max_document_bytes )
        self.backend   = backend or FakeBackend()
        self.discovery = discovery or FakeDiscovery()
        self.renderer  = renderer or FakeRenderer()
        self.lock      = FlockPrintLock( tmp / "print.lock" )
        self.workflow  = PrintWorkflow( self.discovery, self.renderer, self.backend, self.catalog, self.registry,
                                        self.lock, self.clock, self.now, limits )
        self.control   = JobControl( self.discovery, self.backend, self.catalog, self.registry )


def raise_spooler( message: str = "powershell_timeout" ) -> SpoolerError:
    return SpoolerError( message )
