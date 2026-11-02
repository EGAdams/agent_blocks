"""Status, cancel and discovery: the operations that never print.

`status` reads the spooler for a job still in it and records what it saw.
`cancel` stops a job that has not been sent (by compare-and-set, so it either
beats `submit` or loses to it cleanly) or removes one from the Windows queue.
`discover` lists printers, the default, the outbox and recent jobs. None of
them takes the print lock, so they answer while a job is being monitored.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .errors import PrinterError, RequestRefused, StaleState
from .interfaces import IDocumentCatalog, IJobRegistry, IPrintBackend, IPrinterDiscovery
from .models import JOB_ID, TERMINAL, CatalogDocument, JobRecord, JobState, PrinterInfo, PrintReport
from .reports import report

NOT_YET_SENT = frozenset({ JobState.ACCEPTED, JobState.QUEUED })
IN_SPOOLER   = frozenset({ JobState.SUBMITTED_TO_SPOOLER })


class Discovery( BaseModel ):
    model_config = ConfigDict( frozen=True )

    printers:   list[ PrinterInfo ]
    default:    str | None
    documents:  list[ CatalogDocument ]
    recent:     list[ PrintReport ]


class JobControl:
    def __init__( self, discovery: IPrinterDiscovery, backend: IPrintBackend, catalog: IDocumentCatalog,
                  registry: IJobRegistry ) -> None:
        self._discovery = discovery
        self._backend   = backend
        self._catalog   = catalog
        self._registry  = registry

    def discover( self, recent: int = 10 ) -> Discovery:
        printers = self._discovery.list_printers()
        default  = next(( p.printer_id for p in printers if p.is_default ), None )
        return Discovery( printers=printers, default=default, documents=self._catalog.list_documents(),
                          recent=self.recent( recent ))

    def recent( self, limit: int = 10 ) -> list[ PrintReport ]:
        return [ report( r ) for r in self._registry.recent( limit )]

    def status( self, job_id: str ) -> PrintReport:
        record = self._job( job_id )
        if record.state is JobState.SUBMITTED_TO_SPOOLER:
            try:
                seen = self._backend.status( job_id, self._printer( record ))
                record = self._registry.move( job_id, seen.state, seen.detail, expect=IN_SPOOLER,
                                              **({ "pages": seen.pages } if seen.pages else {}))
            except StaleState:
                record = self._job( job_id )
            except PrinterError as exc:
                return report( record ).model_copy( update={ "detail": f"{ record.detail } (status check failed: { exc })" })
        return report( record )

    def cancel( self, job_id: str ) -> PrintReport:
        record = self._job( job_id )
        if record.state in TERMINAL:
            return report( record ).model_copy( update={ "detail": f"already { record.state.value }; nothing to cancel. "
                                                                  f"{ record.detail }" })
        if record.state in NOT_YET_SENT:
            try:
                return report( self._registry.move( job_id, JobState.CANCELED, "canceled before anything was sent to the printer",
                                                    expect=NOT_YET_SENT ))
            except StaleState:
                record = self._job( job_id )        # submit won the race; fall through to the spooler
        if record.state is JobState.SUBMITTING:
            return report( record ).model_copy( update={ "detail": "being handed to the spooler right now; ask again in a "
                                                                  "few seconds" })
        if record.state is JobState.SUBMITTED_TO_SPOOLER:
            result = self._backend.cancel( job_id, self._printer( record ))
            if result.state is JobState.CANCELED:
                try:
                    return report( self._registry.move( job_id, JobState.CANCELED, result.detail, expect=IN_SPOOLER ))
                except StaleState:
                    return report( self._job( job_id ))
            return self.status( job_id ).model_copy( update={ "detail": result.detail })
        return report( record )

    # -- helpers ------------------------------------------------------------------

    def _job( self, job_id: str ) -> JobRecord:
        if not JOB_ID.fullmatch( job_id ):
            raise RequestRefused( "not a print job id" )
        record = self._registry.get( job_id )
        if record is None:
            raise RequestRefused( f"no print job { job_id }" )
        return record

    @staticmethod
    def _printer( record: JobRecord ) -> PrinterInfo:
        return PrinterInfo( printer_id=record.printer_id or "unknown", name=record.printer or "" )
