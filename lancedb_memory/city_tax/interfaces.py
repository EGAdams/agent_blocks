"""Liz's interfaces. Small, single-purpose, `typing.Protocol` only.

Each ADW phase is written against one of these; `adws/liz_adw.py`'s
composition roots are the only places that name the concretes. Swapping an
implementation -- a model-backed field mapper for the deterministic one, a
real federal return for the plan workpaper -- is a change at that root,
never in a phase.

Reused rather than restated: publishing goes through the ADW Generated
`IPageIndex` (adws/adw_interface_category_web_page.py); asking a person goes
through the company to-do list (`company_todos.interfaces.ITodoStore`);
scan arrivals are read off Scala's ledger (`scala.interfaces.IScalaLedger`);
OCR is Scala's `TesseractOcr`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import ( CheckResult, CityReturnDraft, CityTaxRules, Figure, FormDifference, LocatedDocument,
                      OfficialForm, Residency, ScanRequest, SourceRef )


class IOfficialFormProvider( Protocol ):
    """Where the city publishes its blank forms."""

    def list_forms( self ) -> list[ OfficialForm ]: ...
    def download( self, form: OfficialForm, folder: Path ) -> OfficialForm: ...


class ITaxDocumentLocator( Protocol ):
    """Finds the family's own tax documents; never assumes a file name."""

    def locate( self, tax_year: int ) -> list[ LocatedDocument ]: ...


class IPdfTextReader( Protocol ):
    """Every page's text, OCR'd where the text layer is unreadable."""

    def pages( self, pdf: Path ) -> list[ str ]: ...


class ITaxYearComparator( Protocol ):
    def compare( self, before: dict, after: dict ) -> list[ FormDifference ]: ...


class ICityTaxRuleProvider( Protocol ):
    def rules( self, tax_year: int ) -> CityTaxRules: ...


class IFederalTaxRepository( Protocol ):
    """Verified federal figures for one year -- or provisional ones, labelled so."""

    def figures( self, tax_year: int ) -> list[ Figure ]: ...
    def describe( self ) -> str: ...


class IFinanceDataProvider( Protocol ):
    """Read-only supporting evidence from the nonprofit books."""

    def evidence( self, tax_year: int ) -> list[ Figure ]: ...


class ITaxResidencyResolver( Protocol ):
    def resolve( self, tax_year: int ) -> tuple[ Residency, list[ SourceRef ], str ]: ...


class ICityTaxCalculator( Protocol ):
    def calculate( self, figures: list[ Figure ], rules: CityTaxRules, residency: Residency,
                   exemptions: int, payments: Figure ) -> CityReturnDraft: ...


class ITaxReconciler( Protocol ):
    def reconcile( self, draft: CityReturnDraft, federal: list[ Figure ],
                   last_year: list[ Figure ]) -> list[ dict ]: ...


class ITaxFieldMapper( Protocol ):
    """Return lines -> the official PDF's field names and values."""

    def fields( self, draft: CityReturnDraft ) -> dict[ str, str ]: ...


class IPdfFormWriter( Protocol ):
    def write( self, blank: Path, values: dict[ str, str ], out: Path ) -> Path: ...


class IPdfFormValidator( Protocol ):
    def validate( self, filled: Path, expected: dict[ str, str ]) -> list[ CheckResult ]: ...


class IReportPublisher( Protocol ):
    """Puts one finished page on ADW Generated; returns its link name."""

    def publish( self, html: str, stem: str, summary: str ) -> str: ...


class IDocumentScanRequester( Protocol ):
    def request( self, scan: ScanRequest ) -> ScanRequest: ...
    def check( self, scan: ScanRequest ) -> ScanRequest: ...


class IHumanReview( Protocol ):
    """Where an answer from EG is recorded as a source."""

    def record( self, key: str, answer: str ) -> SourceRef: ...


class IWorkpaperStore( Protocol ):
    """Each ADW's output, by name -- the explicit inputs of the next ADW."""

    def save( self, name: str, data: object ) -> Path: ...
    def load( self, name: str ) -> object | None: ...
    def path( self, name: str ) -> Path: ...
