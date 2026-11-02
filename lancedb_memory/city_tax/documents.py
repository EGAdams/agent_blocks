"""The family's own tax documents: finding them, classifying them, and reading last year's city return.

`TaxDocumentFolders` (ITaxDocumentLocator) walks the tax_documents tree --
one folder per scanned page -- and classifies every folder from its name and
its saved summary, never from an assumed file name.

`City2024ReturnReader` reads the completed 2024 CF-1040 off its *image*, not
off the saved JSON extract (two of three extracts checked earlier did not
match their photos; this one's tax line was $20 off the page). It
OCRs the return and its payments worksheet, then accepts the figures only if
the arithmetic chain on the page closes: interest + business = total,
total - exemptions = taxable, taxable x rate = tax, payments - tax = refund.
A chain that doesn't close is reported, and nothing is called verified.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from .models import DocumentClass, Figure, FigureStatus, LocatedDocument, SourceKind, SourceRef
from .store import whole_dollars

TAX_DOCUMENTS = Path.home() / "rol_finances" / "readable_documents" / "tax_documents"

_FEDERAL = re.compile( r"1040|schedule|sched_c|self_emp|ssa|social_security|1099|1098|irs|qbi|earned_income|"
                       r"itemized|carryover|computation|additional|adjustments|7206|9325|2210|other_taxes|"
                       r"profit_or_loss|tax_free|ss_benefits|investment_income|e_file", re.I )


def classify_folder( name: str ) -> DocumentClass:
    low = name.lower()
    if low.startswith( "cf-1040" ):
        return DocumentClass.COMPLETED_TAXPAYER_RETURN
    if "gr_comp" in low or "mi_comp" in low:
        return DocumentClass.CITY_SCHEDULE
    if "cfwk" in low or "miwk" in low or "payment" in low or "ach_" in low or "extension" in low:
        return DocumentClass.PAYMENT_OR_EXTENSION
    if "mi-1040" in low or "michigan" in low:
        return DocumentClass.OTHER_EVIDENCE
    if _FEDERAL.search( low ):
        return DocumentClass.FEDERAL_SUPPORTING
    return DocumentClass.OTHER_EVIDENCE


def _summary_year( folder: Path ) -> tuple[ int | None, str ]:
    for summary in sorted( folder.glob( "*_Summary.json" )):
        try:
            data = json.loads( summary.read_text( encoding="utf-8" ))
        except ( OSError, ValueError ):
            continue
        year = data.get( "tax_year" )
        return ( int( year ) if str( year ).isdigit() else None ), str( data.get( "form", "" ))
    match = re.search( r"(20\d\d)", folder.name )
    return ( int( match.group( 1 )) if match else None ), ""


class TaxDocumentFolders:
    def __init__( self, root: Path = TAX_DOCUMENTS ) -> None:
        self._root = root

    def locate( self, tax_year: int ) -> list[ LocatedDocument ]:
        found: list[ LocatedDocument ] = []
        if not self._root.is_dir():
            return found
        for entry in sorted( self._root.iterdir()):
            if entry.is_dir():
                year, title = _summary_year( entry )
                found.append( LocatedDocument( path=str( entry ), doc_class=classify_folder( entry.name ),
                                               tax_year=year, title=title or entry.name,
                                               note="has concerns.md" if ( entry / "concerns.md" ).exists() else "" ))
            elif entry.suffix.lower() == ".pdf":
                found.append( LocatedDocument( path=str( entry ), doc_class=DocumentClass.FEDERAL_SUPPORTING,
                                               tax_year=None, title=entry.stem ))
        return [ d for d in found if d.tax_year in ( None, tax_year ) or tax_year == 0 ]


# -- last year's return -----------------------------------------------------------

_AMOUNT = re.compile( r"(?<![\d,])(\d{1,3}(?:,\d{3})+|\d{2,6})(?![\d,])" )


def amounts( text: str ) -> set[ Decimal ]:
    return { Decimal( a.replace( ",", "" )) for a in _AMOUNT.findall( text )}


class ChainResult( dict ):
    """interest, business, total, exemptions, exemption_amount, taxable, tax, payments, refund."""


def close_chain( page: str, worksheet: str, rate: Decimal = Decimal( "0.015" ),
                 per_exemption: Decimal = Decimal( "600" )) -> ChainResult | None:
    """The one set of page figures whose arithmetic closes, or None."""
    seen, paid = amounts( page ), amounts( worksheet )
    for total in sorted( seen, reverse=True ):
        for count in range( 1, 11 ):
            exemptions = per_exemption * count
            taxable = total - exemptions
            tax = whole_dollars( taxable * rate )
            if taxable not in seen or tax not in seen:
                continue
            for payments in sorted( paid, reverse=True ):
                refund = payments - tax
                if refund < 0 or ( refund not in seen and refund != 0 ):
                    continue
                parts = sorted( a for a in seen if a < total )
                for business in parts:
                    interest = total - business
                    if interest in seen and interest < business:
                        return ChainResult( interest=interest, business=business, total=total, exemptions=count,
                                            exemption_amount=exemptions, taxable=taxable, tax=tax,
                                            payments=payments, refund=refund )
    return None


class City2024ReturnReader:
    def __init__( self, ocr, root: Path = TAX_DOCUMENTS ) -> None:
        self._ocr = ocr
        self._root = root

    def _image( self, folder: str ) -> Path | None:
        images = sorted(( self._root / folder ).glob( "*.jp*g" ))
        return images[ 0 ] if images else None

    def read( self ) -> tuple[ list[ Figure ], list[ str ]]:
        notes: list[ str ] = []
        page_image = self._image( "CF-1040_grand_rapids_page_1" )
        sheet_image = self._image( "other_tax_payment_worksheet_CFWK_OTP" )
        if page_image is None:
            return [], [ "The completed 2024 Grand Rapids return (CF-1040 page 1) was not found." ]
        page = self._ocr.read( page_image )
        sheet = self._ocr.read( sheet_image ) if sheet_image else ""
        chain = close_chain( page, sheet )
        if chain is None:
            return [], [ "The 2024 return was found, but its figures did not add up as read, so none is used." ]
        ref = SourceRef( kind=SourceKind.TAXPAYER_DOCUMENT, where=str( page_image ), page=1,
                         note="OCR of the completed 2024 CF-1040; arithmetic chain closes" )
        sheet_ref = SourceRef( kind=SourceKind.TAXPAYER_DOCUMENT, where=str( sheet_image ), line="CFWK_OTP line 8" )
        labels = { "interest": ( "2", "Taxable interest" ), "business": ( "4", "Business income (Schedule C)" ),
                   "total": ( "13", "Total income" ), "exemption_amount": ( "16b", "Exemptions" ),
                   "taxable": ( "17", "Income subject to tax" ), "tax": ( "18b", "Grand Rapids tax" ),
                   "payments": ( "19d", "Payments (estimated tax)" ), "refund": ( "25", "Refund" )}
        figures = [ Figure( key=f"gr2024_{ key }", label=f"2024 line { line }: { label }", amount=chain[ key ],
                            status=FigureStatus.VERIFIED,
                            sources=[ sheet_ref if key == "payments" else ref ])
                    for key, ( line, label ) in labels.items()]
        figures.append( Figure( key="gr2024_exemptions", label="2024 line 16a: number of exemptions",
                                amount=Decimal( chain[ "exemptions" ]), status=FigureStatus.VERIFIED, sources=[ ref ]))
        saved = self._root / "CF-1040_grand_rapids_page_1"
        for summary in saved.glob( "*_Summary.json" ):
            try:
                extract = json.loads( summary.read_text( encoding="utf-8" ))
            except ( OSError, ValueError ):
                continue
            said = extract.get( "tax_calculation", {}).get( "line_16_total_tax" )
            if said is not None and Decimal( str( said )) != chain[ "tax" ]:
                notes.append( f"The saved 2024 extract says city tax ${ Decimal( str( said )):,.0f}; the return "
                              f"itself says ${ chain[ 'tax' ]:,.0f}. Liz uses the return." )
        return figures, notes
