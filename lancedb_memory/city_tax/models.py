"""Liz's records. Pydantic, because every one of them crosses an ADW boundary as a workpaper.

Money is `Decimal`, always. A figure that is not known is `None` with a
`MISSING` status -- never zero, never last year's amount.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SourceKind( str, Enum ):
    OFFICIAL_FORM     = "official_form"       # a city / IRS document fetched from its publisher
    TAXPAYER_DOCUMENT = "taxpayer_document"   # a scanned return, statement or letter
    WORKPAPER         = "workpaper"           # the family's tax plan / an earlier ADW's output
    DATABASE          = "database"            # nonprofit_finance, read only
    HUMAN_ANSWER      = "human_answer"        # EG (or Robert / Rosemary) said so
    COMPUTED          = "computed"            # Liz's arithmetic over other sources


class SourceRef( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    kind:     SourceKind
    where:    str                    # path or URL
    page:     int | None = None
    line:     str = ""
    note:     str = ""


class FigureStatus( str, Enum ):
    VERIFIED    = "verified"      # read off a final document, or a settled answer
    PROVISIONAL = "provisional"   # a workpaper figure the federal return has not confirmed yet
    MISSING     = "missing"       # not known; stays blank


class Figure( BaseModel ):
    """One sourced amount. `amount` is None exactly when status is MISSING."""

    model_config = ConfigDict( extra="forbid" )

    key:     str
    label:   str
    amount:  Decimal | None = None
    status:  FigureStatus = FigureStatus.MISSING
    sources: list[ SourceRef ] = Field( default_factory=list )
    note:    str = ""


# -- documents ---------------------------------------------------------------

class FormKind( str, Enum ):
    RETURN       = "return"
    INSTRUCTIONS = "instructions"     # the printable packet: instructions + the return
    EXTENSION    = "extension"
    ESTIMATE     = "estimate"
    OTHER        = "other"


class OfficialForm( BaseModel ):
    """One blank form as the city publishes it. `local_path` is set once downloaded."""

    model_config = ConfigDict( extra="forbid" )

    key:          str                 # "2025-resident-fillable"
    title:        str
    tax_year:     int
    residency:    str = ""            # resident / non-resident / part-year / any
    kind:         FormKind = FormKind.RETURN
    fillable:     bool = False
    url:          str
    listed_on:    str = ""            # the page that linked it
    archived:     bool = False        # True when fetched from the Wayback Machine
    local_path:   str = ""
    sha256:       str = ""
    retrieved_at: datetime | None = None


class DocumentClass( str, Enum ):
    OFFICIAL_BLANK_FORM       = "official_blank_form"
    COMPLETED_TAXPAYER_RETURN = "completed_taxpayer_return"
    FEDERAL_SUPPORTING        = "federal_supporting"
    CITY_SCHEDULE             = "city_schedule"
    PAYMENT_OR_EXTENSION      = "payment_or_extension"
    OTHER_EVIDENCE            = "other_evidence"


class LocatedDocument( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    path:      str
    doc_class: DocumentClass
    tax_year:  int | None = None
    title:     str = ""
    note:      str = ""


# -- rules and comparison ----------------------------------------------------

class CityTaxRules( BaseModel ):
    """One tax year's Grand Rapids rules, as its own official instructions state them."""

    model_config = ConfigDict( extra="forbid" )

    tax_year:            int
    resident_rate:       Decimal
    nonresident_rate:    Decimal
    exemption_amount:    Decimal
    exemption_boxes:     list[ str ]          # the boxes that each count as one exemption
    due_date:            date
    exempt_income:       list[ str ]
    not_deductible:      list[ str ]
    allowable_deductions: list[ str ]
    sources:             list[ SourceRef ] = Field( default_factory=list )


class ChangeKind( str, Enum ):
    UNCHANGED   = "unchanged"      # checked, and the same in both years
    COSMETIC    = "cosmetic"       # wording, layout, a year number moving forward
    SUBSTANTIVE = "substantive"    # a rate, amount, box, line or rule that changes the result
    UNCERTAIN   = "uncertain"      # changed, and the instructions don't say what it means


class FormDifference( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    area:        str                # "Rates", "Form lines", "Instructions", ...
    item:        str
    before:      str = ""           # 2024
    after:       str = ""           # 2025
    kind:        ChangeKind
    explanation: str = ""
    affects_barnes: bool = False
    sources:     list[ SourceRef ] = Field( default_factory=list )


# -- the return ----------------------------------------------------------------

class Residency( str, Enum ):
    RESIDENT    = "resident"
    NONRESIDENT = "nonresident"
    PART_YEAR   = "part_year"
    UNKNOWN     = "unknown"


class ReturnLine( BaseModel ):
    """One line of the city return: federal (A), excluded (B), taxable (C)."""

    model_config = ConfigDict( extra="forbid" )

    line:        str
    label:       str
    column_a:    Decimal | None = None
    column_b:    Decimal | None = None
    column_c:    Decimal | None = None
    explanation: str = ""
    sources:     list[ SourceRef ] = Field( default_factory=list )


class ReturnStatus( str, Enum ):
    DRAFT_PREPARED     = "Draft Prepared"
    NEEDS_DOCUMENTS    = "Needs Documents"
    NEEDS_REVIEW       = "Needs Review"
    READY_FOR_APPROVAL = "Ready for Approval"


class CityReturnDraft( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    tax_year:        int
    residency:       Residency
    filing_status:   str
    exemptions:      int
    lines:           list[ ReturnLine ]
    status:          ReturnStatus
    open_questions:  list[ str ] = Field( default_factory=list )
    assumptions:     list[ str ] = Field( default_factory=list )
    prepared_at:     datetime

    def line( self, number: str ) -> ReturnLine | None:
        return next(( l for l in self.lines if l.line == number ), None )


class CheckResult( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    name:   str
    passed: bool
    detail: str = ""


# -- scans -------------------------------------------------------------------

class ScanState( str, Enum ):
    WAITING   = "waiting"
    RECEIVED  = "received"
    ANSWERED  = "answered"        # EG said there is no such document
    CANCELLED = "cancelled"


class ScanRequest( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    key:          str                  # "gr_2025_payments"
    document:     str                  # what to put in the scanner
    reason:       str                  # why Liz needs it
    figure_key:   str                  # the workpaper figure it settles
    requested_at: datetime
    todo_number:  int | None = None
    state:        ScanState = ScanState.WAITING
    received:     str = ""             # the scan's path
    text_excerpt: str = ""             # first words read off it, for review
    candidates:   list[ str ] = Field( default_factory=list )   # dollar amounts seen; a person picks
    answer:       str = ""             # EG's words when there is no document
    closed_at:    datetime | None = None
