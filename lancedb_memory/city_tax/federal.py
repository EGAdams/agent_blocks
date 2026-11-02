"""The 2025 federal side: is there a filed return, and what figures does Liz use until there is?

`FederalReturnFinder` looks for a completed 2025 federal return -- a
FreeTaxUSA PDF, a 1040 marked filed or accepted -- in the places the family
keeps tax files. Today there is none: filing is on hold for a CPA/EA.

Until there is, `PlanPageFederalRepository` (IFederalTaxRepository) reads the
figures the family's tax plan has *settled* -- each pastor's church pay, the
housing allowance actually paid, Social Security -- off the newest published
"Barnes 2025 Tax Plan" page, and marks every one PROVISIONAL with the reason.
A figure the page doesn't state is MISSING, never zero. When a filed return
exists, a repository over it replaces this one at the composition root and
nothing downstream changes.
"""
from __future__ import annotations

import html
import json
import re
from decimal import Decimal
from pathlib import Path

from .models import Figure, FigureStatus, LocatedDocument, DocumentClass, SourceKind, SourceRef

WEB_PAGES = Path.home() / "agent_blocks" / "adws" / "adw_data" / "web_pages"
PLAN_MARKDOWN = Path.home() / "rol_finances" / "barnes_2025_tax_preparation_plan.md"
_SEARCH = ( Path.home() / "rol_finances", Path( "/mnt/c/Users/NewUser/Downloads" ),
            Path( "/mnt/c/Users/NewUser/Documents" ))
_RETURN_NAME = re.compile( r"(2025.*(1040|freetax|federal|tax.?return))|((1040|freetax).*2025)", re.I )


class FederalReturnFinder:
    def __init__( self, roots: tuple[ Path, ... ] = _SEARCH, max_depth: int = 4 ) -> None:
        self._roots = roots
        self._depth = max_depth

    def locate( self, tax_year: int ) -> list[ LocatedDocument ]:
        found: list[ LocatedDocument ] = []
        for root in self._roots:
            if not root.is_dir():
                continue
            for path in root.rglob( "*.pdf" ):
                if len( path.relative_to( root ).parts ) > self._depth or ".git" in path.parts:
                    continue
                if str( tax_year ) in path.name and _RETURN_NAME.search( path.name ):
                    found.append( LocatedDocument( path=str( path ), doc_class=DocumentClass.FEDERAL_SUPPORTING,
                                                   tax_year=tax_year, title=path.name,
                                                   note="candidate -- final only if it says it was filed" ))
        return found


def _page_text( path: Path ) -> str:
    raw = path.read_text( encoding="utf-8", errors="replace" )
    raw = re.sub( r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S )
    return re.sub( r"\s+", " ", html.unescape( re.sub( r"<[^>]+>", " ", raw )))


def newest_plan_page( folder: Path = WEB_PAGES ) -> Path | None:
    try:
        listed = json.loads(( folder / "index.json" ).read_text( encoding="utf-8" ))
    except ( OSError, ValueError ):
        listed = []
    for entry in listed:                     # newest first
        name = str( entry.get( "file", "" ))
        if name.startswith( "Barnes_2025_Tax_Plan_" ) and ( folder / name ).is_file():
            return folder / name
    return None


_MONEY = r"\$([\d,]+\.\d\d)"


def _pair( text: str, label: str ) -> tuple[ Decimal, Decimal ] | None:
    match = re.search( re.escape( label ) + r"\s*" + _MONEY + r"\s*(?:/\s*)?" + _MONEY, text )
    if not match:
        return None
    return Decimal( match.group( 1 ).replace( ",", "" )), Decimal( match.group( 2 ).replace( ",", "" ))


class PlanPageFederalRepository:
    """IFederalTaxRepository over the family's published plan -- provisional by construction."""

    CAVEAT = ( "Settled in the family's tax plan, but the 2025 federal return is not filed: it is on hold "
               "for a CPA/EA (employee or self-employed, van values)." )

    def __init__( self, page: Path | None = None, markdown: Path = PLAN_MARKDOWN ) -> None:
        self._page = page or newest_plan_page()
        self._markdown = markdown

    def describe( self ) -> str:
        return f"the published tax plan page { self._page.name if self._page else '(none found)' }"

    def figures( self, tax_year: int ) -> list[ Figure ]:
        if tax_year != 2025 or self._page is None:
            return []
        text = _page_text( self._page )
        ref = lambda line: SourceRef( kind=SourceKind.WORKPAPER, where=str( self._page ), line=line )
        out: list[ Figure ] = []

        def both( key: str, label: str, anchor: str, status: FigureStatus, note: str = "" ) -> None:
            pair = _pair( text, anchor )
            for who, index in (( "robert", 0 ), ( "rosemary", 1 )):
                amount = pair[ index ] if pair else None
                where = f"{ who.title()}'s { label.split( ' (' )[ 0 ].lower()} in the plan's church-pay table"
                out.append( Figure( key=f"{ key }_{ who }", label=f"{ label } -- { who.title()}", amount=amount,
                                    status=status if amount is not None else FigureStatus.MISSING,
                                    sources=[ ref( where )] if amount is not None else [],
                                    note=note if amount is not None else f"'{ anchor }' not found on the plan page" ))

        both( "church_pay", "Church pay (taxable, housing not included)", "Enter this",
              FigureStatus.PROVISIONAL, self.CAVEAT )
        for anchor, key, label in (( "Salary", "salary", "Salary" ),
                                   ( "SE tax paid back", "se_tax_paid_back", "2024 SE tax the church paid back" ),
                                   ( "Offerings (includes the $468 anniversary)", "offerings", "Offerings" ),
                                   ( "Wedding fee", "wedding_fee", "Wedding fee" ),
                                   ( "Personal van use", "van_personal_use", "Personal use of a church van" ),
                                   ( "Priority Health insurance", "priority_health", "Health insurance paid by church" ),
                                   ( "Medical paid by church", "medical_paid", "Medical bills paid by church" )):
            both( key, f"{ label } (inside church pay)", anchor, FigureStatus.PROVISIONAL,
                  "A part of church pay, listed so a difference can be traced; never added twice." )
        if m := re.search( r"TruStage life insurance\s*" + _MONEY, text ):
            out.append( Figure( key="trustage_robert", label="TruStage life insurance (inside church pay) -- Robert",
                                amount=Decimal( m.group( 1 ).replace( ",", "" )), status=FigureStatus.PROVISIONAL,
                                sources=[ ref( "TruStage life insurance" )], note="Robert's policy; Rosemary has none." ))
        both( "social_security", "Social Security benefits", "Social Security — Robert / Rosemary",
              FigureStatus.VERIFIED, "From the year-end statements; not taxed by Grand Rapids." )
        both( "medicare_part_b", "Medicare Part B", "Medicare Part B — Robert / Rosemary", FigureStatus.VERIFIED )
        if m := re.search( r"Housing allowance actually paid:\s*" + _MONEY, text ):
            out.append( Figure( key="housing_allowance_paid", label="Housing allowance actually paid (Robert)",
                                amount=Decimal( m.group( 1 ).replace( ",", "" )), status=FigureStatus.PROVISIONAL,
                                sources=[ ref( "Housing allowance actually paid" )],
                                note="Excluded from federal income tax under IRC 107; counted for SE tax only." ))
        if m := re.search( r"Robert\s*" + _MONEY + r"\s*\(Priority Health \$[\d,.]+ \+ Medicare Part B \$[\d,.]+\)"
                           r"\s*and Rosemary\s*" + _MONEY, text ):
            for who, value in (( "robert", m.group( 1 )), ( "rosemary", m.group( 2 ))):
                out.append( Figure( key=f"se_health_insurance_{ who }", label=f"Self-employed health insurance "
                                    f"deduction -- { who.title()}", amount=Decimal( value.replace( ",", "" )),
                                    status=FigureStatus.PROVISIONAL, sources=[ ref( "Decided 2026-10-29: health insurance" )],
                                    note="A federal adjustment Grand Rapids does not allow." ))
        out.append( self._interest())
        return out

    def _interest( self ) -> Figure:
        try:
            text = self._markdown.read_text( encoding="utf-8" )
        except OSError:
            text = ""
        if m := re.search( r"1099-INT \$([\d,]+\.\d\d)", text ):
            return Figure( key="taxable_interest", label="Taxable interest (1099-INT, West Michigan Credit Union)",
                           amount=Decimal( m.group( 1 ).replace( ",", "" )), status=FigureStatus.VERIFIED,
                           sources=[ SourceRef( kind=SourceKind.WORKPAPER, where=str( self._markdown ),
                                                line="IRS wage and income transcript (Rosemary's SSN)" )],
                           note="Only forms under Rosemary's SSN were on the transcript; any interest under "
                                "Robert's SSN is not known." )
        return Figure( key="taxable_interest", label="Taxable interest", note="not found in the plan" )
