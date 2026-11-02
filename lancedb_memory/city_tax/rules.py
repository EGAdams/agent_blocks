"""Year-specific Grand Rapids rules, residency, and exemptions -- each from a cited source.

`FactsRuleProvider` (ICityTaxRuleProvider) builds a year's `CityTaxRules`
from the `YearFacts` read off that year's official packet. A rule the packet
doesn't state raises: the calculator never falls back to a remembered rate.

`DocumentResidencyResolver` (ITaxResidencyResolver) never infers residency
from a ZIP code. It needs the prior city return filed as a resident AND the
same home address carried into the tax year; with either missing the answer
is UNKNOWN. The city's own residency test is its street directory, which
refuses automated requests, so the result always carries that caveat.

`exemptions_for` counts the boxes the form allows -- one each, plus one for
65 or over -- from birth dates in the family's records.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from .compare import YearFacts
from .models import CityTaxRules, Residency, SourceKind, SourceRef
from .store import whole_dollars

PLAN_MARKDOWN = Path.home() / "rol_finances" / "barnes_2025_tax_preparation_plan.md"
TAX_SKILL = Path.home() / "rol_finances" / ".skills" / "barnes-tax-preparation" / "SKILL.md"


class MissingRule( ValueError ):
    pass


def rules_from_facts( facts: YearFacts ) -> CityTaxRules:
    missing = [ name for name in ( "resident_rate", "nonresident_rate", "exemption_amount", "due_date" )
                if getattr( facts, name ) is None ]
    if missing:
        raise MissingRule( f"The { facts.tax_year } packet does not state: { ', '.join( missing )}." )
    return CityTaxRules( tax_year=facts.tax_year, resident_rate=facts.resident_rate,
                         nonresident_rate=facts.nonresident_rate, exemption_amount=facts.exemption_amount,
                         exemption_boxes=facts.exemption_boxes, due_date=facts.due_date,
                         exempt_income=facts.exempt_income, not_deductible=facts.not_deductible,
                         allowable_deductions=facts.deductions,
                         sources=[ SourceRef( kind=SourceKind.OFFICIAL_FORM, where=facts.source,
                                              note=f"{ facts.tax_year } official resident instructions" )])


class FactsRuleProvider:
    def __init__( self, facts_by_year: dict[ int, dict ]) -> None:
        self._facts = facts_by_year

    def rules( self, tax_year: int ) -> CityTaxRules:
        if tax_year not in self._facts:
            raise MissingRule( f"No official { tax_year } packet has been read yet." )
        return rules_from_facts( YearFacts.model_validate( self._facts[ tax_year ]))


def birth_dates( markdown: Path = PLAN_MARKDOWN ) -> tuple[ dict[ str, date ], SourceRef | None ]:
    try:
        text = markdown.read_text( encoding="utf-8" )
    except OSError:
        return {}, None
    found = { who.lower(): date( int( y ), int( m ), int( d ))
              for who, m, d, y in re.findall( r"(Robert|Rosemary) (\d{1,2})/(\d{1,2})/(\d{4})", text )}
    return found, SourceRef( kind=SourceKind.WORKPAPER, where=str( markdown ), line="Birthdates" )


def exemptions_for( births: dict[ str, date ], tax_year: int, boxes: list[ str ]) -> int:
    """Regular + (65 or over at year end). Blind is never assumed."""
    count = 0
    for born in births.values():
        count += 1
        if "65 or over" in boxes and date( tax_year, 12, 31 ) >= date( born.year + 65, born.month, born.day ):
            count += 1
    return count


class DocumentResidencyResolver:
    def __init__( self, last_return_text: str, last_return_path: str, address_sources: tuple[ Path, ... ] =
                  ( PLAN_MARKDOWN, TAX_SKILL ), prior_tax: Decimal | None = None,
                  prior_taxable: Decimal | None = None, prior_resident_rate: Decimal | None = None ) -> None:
        self._text = last_return_text
        self._path = last_return_path
        self._addresses = address_sources
        self._prior = ( prior_tax, prior_taxable, prior_resident_rate )

    def resolve( self, tax_year: int ) -> tuple[ Residency, list[ SourceRef ], str ]:
        refs: list[ SourceRef ] = []
        resident_code = re.search( rf"{ str( tax_year - 1 )[ 2: ]}MI-?GRR-?R-?1040", self._text.replace( " ", "" ))
        if resident_code:
            refs.append( SourceRef( kind=SourceKind.TAXPAYER_DOCUMENT, where=self._path, page=1,
                                    note=f"{ tax_year - 1 } return printed with the resident form code "
                                         f"{ resident_code.group( 0 )}" ))
        tax, taxable, rate = self._prior
        if not resident_code and None not in self._prior and tax == whole_dollars( taxable * rate ):
            resident_code = True
            refs.append( SourceRef( kind=SourceKind.TAXPAYER_DOCUMENT, where=self._path, page=1, line="18b",
                                    note=f"{ tax_year - 1 } city tax ${ tax:,.0f} is exactly the resident rate "
                                         f"({ rate * 100:.1f}%) on ${ taxable:,.0f}: it was filed as a resident" ))
        address = None
        for path in self._addresses:
            try:
                text = path.read_text( encoding="utf-8" )
            except OSError:
                continue
            if m := re.search( r"\d{3,5} [A-Z][a-z]+ (?:NW|NE|SW|SE)?,? Grand Rapids,? MI 495\d\d", text ):
                address = m.group( 0 )
                refs.append( SourceRef( kind=SourceKind.WORKPAPER, where=str( path ),
                                        note="home address recorded for the family (not repeated in reports)" ))
                break
        caveat = ( "The city's test is its street directory, which refuses automated requests; a person should "
                   "confirm the address is inside the city and that neither of them moved during the year." )
        if resident_code and address:
            return Residency.RESIDENT, refs, caveat
        return Residency.UNKNOWN, refs, "Not enough evidence: " + caveat
