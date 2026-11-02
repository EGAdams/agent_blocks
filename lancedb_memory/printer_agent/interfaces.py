"""The printer agent's interfaces. `typing.Protocol`, `I` prefix, nothing concrete.

A new kind of printer (CUPS, a network IPP printer driven directly) is a new
`IPrintBackend` + `IPrinterDiscovery` pair beside `adapters/windows_spooler.py`;
a new document type is a new `IDocumentRenderer` registered by kind. Nothing
here changes for either.
"""
from __future__ import annotations

from typing import Protocol

from .models import ( CatalogDocument, CommandOutput, JobRecord, JobState, PrintDocument, PrinterInfo, PrintJob,
                      PrintOptions, PrintResult, PrintStatus, RenderedDocument )


class IPrinterDiscovery( Protocol ):
    """Read-only: lists queues. Never installs, configures or prints."""

    def list_printers( self ) -> list[ PrinterInfo ]: ...


class IDocumentRenderer( Protocol ):
    def render( self, document: PrintDocument, options: PrintOptions ) -> RenderedDocument: ...


class IPrintBackend( Protocol ):
    def submit( self, job: PrintJob ) -> PrintResult: ...

    def status( self, job_id: str, printer: PrinterInfo ) -> PrintStatus: ...

    def cancel( self, job_id: str, printer: PrinterInfo ) -> PrintResult: ...


class IDocumentCatalog( Protocol ):
    """The approved outbox. Ids in, bytes out; a path is never accepted from a caller."""

    def list_documents( self ) -> list[ CatalogDocument ]: ...

    def read( self, document_id: str ) -> tuple[ CatalogDocument, bytes ]: ...


class IJobRegistry( Protocol ):
    """Durable job state, keyed by job id (the bridge task id). Holds no document content."""

    def get( self, job_id: str ) -> JobRecord | None: ...

    def begin( self, record: JobRecord ) -> tuple[ JobRecord, bool ]: ...      # ( the row, True if newly created )

    def move( self, job_id: str, state: JobState, detail: str = "", expect: frozenset[ JobState ] | None = None,
              **fields: object ) -> JobRecord: ...

    def recent( self, limit: int = 20 ) -> list[ JobRecord ]: ...

    def submitted_since( self, seconds: float ) -> int: ...

    def recover( self ) -> list[ str ]: ...


class IPrintLock( Protocol ):
    """One print at a time. `acquire` raises PrinterBusy instead of waiting."""

    def acquire( self ) -> None: ...

    def release( self ) -> None: ...


class ICommandRunner( Protocol ):
    """Runs one fixed program with an argv list -- never a shell, never a string built from a request."""

    def run( self, argv: list[ str ], timeout: float, stdin: bytes | None = None ) -> CommandOutput: ...


class IClock( Protocol ):
    def monotonic( self ) -> float: ...

    def sleep( self, seconds: float ) -> None: ...
