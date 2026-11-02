"""PrintWorkflow: one print request, as the seven steps the ADW shows as phases.

    receive -> validate -> resolve_printer -> render -> submit -> monitor -> report

Each step is a public method so the ADW can run it as its own phase, and
`run()` chains them for callers that want ordinary code. The rules that make
this safe to drive from a remote chat live here, not in the ADW:

* The job id is the bridge task id. `receive` registers it once; a second run
  with the same id gets the first run's record back and prints nothing.
* Every refusal before `submit` leaves the job `failed` with nothing sent.
* `submit` moves the job to `submitting` *before* calling the spooler, by
  compare-and-set from `queued`, so a cancel that landed first wins and a
  crash mid-call is found later as `ambiguous` -- never reprinted.
* `monitor` only reads the spooler, stops at the time limit leaving the job
  `submitted_to_spooler`, and never overwrites a state someone else set.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime

from .errors import PrinterError, RequestRefused, SpoolerRefused, StaleState
from .interfaces import ( IClock, IDocumentCatalog, IDocumentRenderer, IJobRegistry, IPrintBackend, IPrinterDiscovery,
                         IPrintLock )
from .models import ( TERMINAL, JobRecord, JobState, PrintDocument, PrinterInfo, PrintJob, PrintLimits, PrintReport,
                     PrintRequest, RenderedDocument )
from .reports import TEST_PAGE_TITLE, parse_request, report, test_page_text

QUEUED_ONLY  = frozenset({ JobState.QUEUED })
NOT_YET_SENT = frozenset({ JobState.ACCEPTED, JobState.QUEUED })
IN_SPOOLER   = frozenset({ JobState.SUBMITTED_TO_SPOOLER })


class PrintWorkflow:
    def __init__( self, discovery: IPrinterDiscovery, renderer: IDocumentRenderer, backend: IPrintBackend,
                  catalog: IDocumentCatalog, registry: IJobRegistry, lock: IPrintLock, clock: IClock,
                  now: Callable[[], datetime ], limits: PrintLimits = PrintLimits()) -> None:
        self._discovery = discovery
        self._renderer  = renderer
        self._backend   = backend
        self._catalog   = catalog
        self._registry  = registry
        self._lock      = lock
        self._clock     = clock
        self._now       = now
        self._limits    = limits

    # -- the whole thing, for ordinary callers ---------------------------------

    def acquire( self ) -> None:
        """Take the one-job-at-a-time lock, then settle jobs a dead run left behind. Pair with `release()`."""
        self._lock.acquire()
        try:
            self._registry.recover()
        except BaseException:
            self._lock.release()
            raise

    def release( self ) -> None:
        self._lock.release()

    def run( self, job_id: str, prompt: str ) -> PrintReport:
        self.acquire()
        try:
            record, request = self.receive( job_id, prompt )
            if request is None:
                return report( record )
            try:
                document = self.validate( job_id, request )
                printer  = self.resolve_printer( job_id, request )
                rendered = self.render( job_id, document, request )
            except PrinterError as exc:
                return report( self.refuse( job_id, exc ))
            self.submit( job_id, printer, document, rendered, request )
            self.monitor( job_id, printer )
            return self.report( job_id )
        finally:
            self.release()

    # -- phases ----------------------------------------------------------------

    def receive( self, job_id: str, prompt: str ) -> tuple[ JobRecord, PrintRequest | None ]:
        """Register the job. Returns ( record, None ) when there is nothing (more) to do: a duplicate or a bad request."""
        try:
            request: PrintRequest | None = parse_request( prompt )
            detail = "request accepted"
        except RequestRefused as exc:
            request, detail = None, str( exc )
        title = ( TEST_PAGE_TITLE if request and request.kind == "test_page" else ( request.title if request else "" ))
        record, created = self._registry.begin( JobRecord( job_id=job_id, state=JobState.ACCEPTED,
                                                           kind=request.kind if request else "invalid", title=title,
                                                           copies=request.options.copies if request else 1,
                                                           detail=detail, created_at=self._now(), updated_at=self._now()))
        if not created:
            return record, None          # a repeat of a job already registered: report it, print nothing
        if request is None:
            return self._registry.move( job_id, JobState.FAILED, detail, expect=NOT_YET_SENT ), None
        return record, request

    def validate( self, job_id: str, request: PrintRequest ) -> PrintDocument:
        if request.kind == "test_page":
            data, kind, title, source = test_page_text( self._now()).encode( "utf-8" ), "text", TEST_PAGE_TITLE, "test_page"
        elif request.kind == "text":
            if len( request.text or "" ) > self._limits.max_text_chars:
                raise RequestRefused( f"text prints are limited to { self._limits.max_text_chars } characters" )
            data, kind, title, source = ( request.text or "" ).encode( "utf-8" ), "text", request.title or "Text", "inline"
        else:
            entry, data = self._catalog.read( request.document_id or "" )
            kind  = "pdf" if entry.mime == "application/pdf" else "text"
            title, source = request.title or entry.name, entry.document_id
        if len( data ) > self._limits.max_document_bytes:
            raise RequestRefused( "the document is larger than the size limit" )
        document = PrintDocument( kind=kind, title=title, data=data, sha256=hashlib.sha256( data ).hexdigest(), source=source )
        self._registry.move( job_id, JobState.ACCEPTED, "document validated", expect=NOT_YET_SENT,
                             sha256=document.sha256, bytes=len( data ))
        return document

    def resolve_printer( self, job_id: str, request: PrintRequest ) -> PrinterInfo:
        printers = self._discovery.list_printers()
        if request.printer_id is None:
            chosen = next(( p for p in printers if p.is_default ), None )
            if chosen is None:
                raise RequestRefused( "no default printer is set; name one of printer_list's ids" )
        else:
            chosen = next(( p for p in printers if p.printer_id == request.printer_id ), None )
            if chosen is None:
                raise RequestRefused( f"unknown printer { request.printer_id }; use one of printer_list's ids" )
        self._registry.move( job_id, JobState.ACCEPTED, f"printer { chosen.printer_id }", expect=NOT_YET_SENT,
                             printer_id=chosen.printer_id, printer=chosen.name )
        return chosen

    def render( self, job_id: str, document: PrintDocument, request: PrintRequest ) -> RenderedDocument:
        rendered = self._renderer.render( document, request.options )
        if not rendered.pages:
            raise RequestRefused( "the document has no pages to print" )
        self._registry.move( job_id, JobState.QUEUED, f"{ len( rendered.pages )} page(s) ready", expect=NOT_YET_SENT,
                             pages=len( rendered.pages ))
        return rendered

    def submit( self, job_id: str, printer: PrinterInfo, document: PrintDocument, rendered: RenderedDocument,
                request: PrintRequest ) -> JobRecord:
        if self._registry.submitted_since( 3600.0 ) >= self._limits.max_jobs_per_hour:
            return self._registry.move( job_id, JobState.FAILED,
                                        f"the limit of { self._limits.max_jobs_per_hour } print jobs per hour was reached",
                                        expect=QUEUED_ONLY )
        try:
            self._registry.move( job_id, JobState.SUBMITTING, "handing the job to the spooler", expect=QUEUED_ONLY )
        except StaleState:
            return self._registry.get( job_id )   # type: ignore[return-value]   -- canceled before it was sent
        job = PrintJob( job_id=job_id, printer=printer, title=document.title, rendered=rendered, options=request.options )
        try:
            result = self._backend.submit( job )
        except SpoolerRefused as exc:
            return self._registry.move( job_id, JobState.FAILED, f"the spooler refused the job: { exc }",
                                        expect=frozenset({ JobState.SUBMITTING }))
        except Exception as exc:  # noqa: BLE001 -- anything else may have printed
            return self._registry.move( job_id, JobState.AMBIGUOUS,
                                        f"the spooler call failed ({ type( exc ).__name__ }: { str( exc )[ :120 ]}); the job "
                                        "may or may not have printed and will not be reprinted automatically",
                                        expect=frozenset({ JobState.SUBMITTING }))
        return self._registry.move( job_id, result.state, result.detail, expect=frozenset({ JobState.SUBMITTING }),
                                    spooler_id=result.spooler_id )

    def monitor( self, job_id: str, printer: PrinterInfo ) -> JobRecord:
        """Read the spooler until the job leaves it or time runs out. Reads only; never resubmits."""
        deadline = self._clock.monotonic() + self._limits.monitor_seconds
        record = self._registry.get( job_id )
        while record is not None and record.state is JobState.SUBMITTED_TO_SPOOLER:
            if self._clock.monotonic() >= deadline:
                return self._registry.move( job_id, JobState.SUBMITTED_TO_SPOOLER,
                                            f"still in the Windows queue after { int( self._limits.monitor_seconds )} s; "
                                            "ask printer_get_status later", expect=IN_SPOOLER )
            self._clock.sleep( self._limits.poll_seconds )
            try:
                status = self._backend.status( job_id, printer )
            except PrinterError as exc:
                return self._registry.move( job_id, JobState.SUBMITTED_TO_SPOOLER,
                                            f"submitted; the status check failed ({ exc }); ask printer_get_status later",
                                            expect=IN_SPOOLER )
            try:
                record = self._registry.move( job_id, status.state, status.detail, expect=IN_SPOOLER,
                                              **({ "pages": status.pages } if status.pages else {}))
            except StaleState:
                return self._registry.get( job_id )   # type: ignore[return-value]   -- canceled meanwhile
        return record   # type: ignore[return-value]

    def report( self, job_id: str ) -> PrintReport:
        record = self._registry.get( job_id )
        if record is None:
            raise RequestRefused( f"no print job { job_id }" )
        return report( record )

    def refuse( self, job_id: str, exc: Exception ) -> JobRecord:
        """Close a job that failed before submission. Nothing was sent, so `failed` is the truth."""
        return self._registry.move( job_id, JobState.FAILED, str( exc )[ :500 ], expect=NOT_YET_SENT )


def is_finished( record: JobRecord ) -> bool:
    return record.state in TERMINAL
