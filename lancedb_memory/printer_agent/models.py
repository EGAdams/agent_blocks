"""The data that crosses the printer agent's boundaries. Pydantic only.

Nothing here knows about Windows, Pillow, the gateway or the worker. A print
job's lifecycle is `JobState`; the words are the spooler's view of the job,
never a claim that paper came out of the printer -- `completed_per_spooler`
means the Windows queue released the job with no error, nothing more.
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PRINTER_ID  = re.compile( r"[a-z0-9][a-z0-9_]{0,63}" )
DOCUMENT_ID = re.compile( r"doc_[0-9a-f]{16}" )
PAGE_RANGE  = re.compile( r"\d{1,3}(-\d{1,3})?(,\d{1,3}(-\d{1,3})?){0,9}" )
JOB_ID      = re.compile( r"[A-Za-z0-9_\-]{6,80}" )


class JobState( str, Enum ):
    ACCEPTED              = "accepted"               # validated, registered, nothing sent yet
    QUEUED                = "queued"                 # rendered, waiting for the one print slot
    SUBMITTING            = "submitting"             # handed to the spooler; outcome not yet known
    SUBMITTED_TO_SPOOLER  = "submitted_to_spooler"   # the Windows queue holds it
    COMPLETED_PER_SPOOLER = "completed_per_spooler"  # the queue released it with no error seen
    CANCELED              = "canceled"
    FAILED                = "failed"
    AMBIGUOUS             = "ambiguous"              # may or may not have printed; never reprinted automatically


TERMINAL = frozenset({ JobState.COMPLETED_PER_SPOOLER, JobState.CANCELED, JobState.FAILED, JobState.AMBIGUOUS })
# Once a job may have reached the spooler it must never be submitted again.
REACHED_SPOOLER = frozenset({ JobState.SUBMITTING, JobState.SUBMITTED_TO_SPOOLER, JobState.COMPLETED_PER_SPOOLER,
                              JobState.AMBIGUOUS })


class PrinterInfo( BaseModel ):
    model_config = ConfigDict( frozen=True )

    printer_id: str                 # stable, derived from the Windows name; the only thing callers pass
    name:       str                 # the Windows queue name
    driver:     str = ""
    is_default: bool = False
    status:     str = "unknown"     # Get-Printer's PrinterStatus, as text
    shared:     bool = False


class PrintOptions( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    copies:      int = Field( default=1, ge=1, le=10 )
    page_range:  str | None = None
    orientation: Literal[ "portrait", "landscape" ] = "portrait"
    color:       Literal[ "monochrome" ] = "monochrome"      # colour is not offered yet
    paper:       Literal[ "letter" ] = "letter"
    duplex:      Literal[ "simplex" ] = "simplex"

    @field_validator( "page_range" )
    @classmethod
    def _range( cls, value: str | None ) -> str | None:
        if value is None or value.strip() == "":
            return None
        value = value.replace( " ", "" )
        if not PAGE_RANGE.fullmatch( value ):
            raise ValueError( "page_range looks like 1-3 or 1,3,5-7" )
        return value


class PrintRequest( BaseModel ):
    """What a task asks for, parsed from its prompt. Either text or a catalog document, never a path."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    kind:        Literal[ "text", "document", "test_page" ]
    title:       str = Field( default="", max_length=120 )
    text:        str | None = Field( default=None, max_length=6000 )
    document_id: str | None = None
    printer_id:  str | None = None              # None: the default printer
    options:     PrintOptions = PrintOptions()

    @field_validator( "printer_id" )
    @classmethod
    def _printer( cls, value: str | None ) -> str | None:
        if value is not None and not PRINTER_ID.fullmatch( value ):
            raise ValueError( "printer_id is one of the ids printer_list returns" )
        return value

    @field_validator( "document_id" )
    @classmethod
    def _document( cls, value: str | None ) -> str | None:
        if value is not None and not DOCUMENT_ID.fullmatch( value ):
            raise ValueError( "document_id is one of the ids printer_list_documents returns" )
        return value


class CatalogDocument( BaseModel ):
    """One file in the approved print outbox. The path never leaves this machine."""

    model_config = ConfigDict( frozen=True )

    document_id: str
    name:        str
    mime:        Literal[ "application/pdf", "text/plain" ]
    bytes:       int
    sha256:      str
    pages:       int | None = None
    modified_at: datetime


class PrintDocument( BaseModel ):
    """A validated document, ready to render. `data` is held in memory only."""

    model_config = ConfigDict( frozen=True )

    kind:   Literal[ "text", "pdf" ]
    title:  str
    data:   bytes
    sha256: str
    source: str              # "inline", "test_page" or a document id -- never a path


class RenderedPage( BaseModel ):
    model_config = ConfigDict( frozen=True )

    png: bytes


class RenderedDocument( BaseModel ):
    model_config = ConfigDict( frozen=True )

    pages: list[ RenderedPage ]
    dpi:   int = 300


class PrintJob( BaseModel ):
    model_config = ConfigDict( frozen=True )

    job_id:   str
    printer:  PrinterInfo
    title:    str
    rendered: RenderedDocument
    options:  PrintOptions


class PrintResult( BaseModel ):
    model_config = ConfigDict( frozen=True )

    state:      JobState
    spooler_id: int | None = None
    detail:     str = ""


class PrintStatus( BaseModel ):
    model_config = ConfigDict( frozen=True )

    state:     JobState
    in_queue:  bool
    flags:     list[ str ] = []     # Get-PrintJob's JobStatus words, e.g. Printing, Error, PaperOut
    pages:     int | None = None
    detail:    str = ""


class JobRecord( BaseModel ):
    """One row of the durable job registry. Never holds document content."""

    model_config = ConfigDict( frozen=True )

    job_id:      str
    state:       JobState
    kind:        str
    title:       str
    printer_id:  str | None = None
    printer:     str | None = None
    copies:      int = 1
    pages:       int | None = None
    sha256:      str = ""
    bytes:       int = 0
    spooler_id:  int | None = None
    detail:      str = ""
    created_at:  datetime
    updated_at:  datetime
    submitted_at: datetime | None = None    # when it was handed to the spooler, if it was


class PrintLimits( BaseModel ):
    model_config = ConfigDict( frozen=True )

    max_pages:          int = 50
    max_copies:         int = 10
    max_document_bytes: int = 20 * 1024 * 1024
    max_text_chars:     int = 6000
    max_jobs_per_hour:  int = 10
    monitor_seconds:    float = 300.0
    poll_seconds:       float = 3.0
    retention_hours:    float = 24.0


class CommandOutput( BaseModel ):
    model_config = ConfigDict( frozen=True )

    returncode: int
    stdout:     bytes = b""
    stderr:     bytes = b""


class PrintReport( BaseModel ):
    """What the ADW reports: the job as the registry and spooler see it, and what that does not prove."""

    model_config = ConfigDict( frozen=True )

    job_id:     str
    state:      JobState
    printer_id: str | None = None
    printer:    str | None = None
    title:      str = ""
    pages:      int | None = None
    copies:     int = 1
    sha256:     str = ""
    spooler_id: int | None = None
    detail:     str = ""
    physical_output_verified: bool = False     # always False: no printer here confirms paper
    submitted_at: datetime | None = None
