"""Reading one year's official packet into facts, and comparing two years' facts.

`read_year` turns a packet's page texts into `YearFacts`: the rule values the
instructions state (rates, exemption amount, due date, exempt income, the
federal adjustments the city refuses), the return's line labels, its
exemption boxes, its form id, and the instructions as normalized sentences.
Every value is read from the text; one the text doesn't state stays None and
shows up in the comparison as "not stated", never as a change.

`FactsComparator` (ITaxYearComparator) decides what kind each difference is:

* a rate, an exemption amount, an exemption box, a line that appears or
  disappears, an exempt-income or not-deductible item -> SUBSTANTIVE
  (it changes a number on somebody's return);
* a line whose number stays and whose words change, a sentence that
  changed only by its year, the packet's cover -> COSMETIC;
* a whole sentence that is new or gone -> UNCERTAIN, quoted, for a person
  to read. Wording alone is never reported as a change in the law.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .models import ChangeKind, FormDifference, SourceKind, SourceRef

_BOXES = ( "Regular", "65 or over", "Blind", "Deaf", "Disabled" )


class YearFacts( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    tax_year:          int
    source:            str
    form_id:           str = ""
    title:             str = ""
    resident_rate:     Decimal | None = None
    nonresident_rate:  Decimal | None = None
    exemption_amount:  Decimal | None = None
    due_date:          date | None = None
    exemption_boxes:   list[ str ] = Field( default_factory=list )
    exempt_income:     list[ str ] = Field( default_factory=list )
    not_deductible:    list[ str ] = Field( default_factory=list )
    deductions:        list[ str ] = Field( default_factory=list )
    form_lines:        dict[ str, str ] = Field( default_factory=dict )
    form_text:         str = ""
    sentences:         list[ str ] = Field( default_factory=list )
    ocr_pages:         list[ int ] = Field( default_factory=list )


def _squash( text: str ) -> str:
    return re.sub( r"\s+", " ", text ).strip()


def _between( text: str, start: str, end: str ) -> str:
    """The text between two phrases, matching any run of whitespace inside them (columns wrap)."""
    loose = lambda phrase: r"\s+".join( re.escape( word ) for word in phrase.split())
    match = re.search( loose( start ) + r"(.*?)" + loose( end ), text, re.S | re.I )
    return _squash( match.group( 1 )) if match else ""


def _numbered( block: str ) -> list[ str ]:
    parts = re.split( r"(?:^|\s)(\d)\.?\s+(?=[A-Z])", block )
    items = [ _squash( parts[ i + 1 ]) for i in range( 1, len( parts ) - 1, 2 )]
    return [ re.sub( r"\s*\(.*?\)\s*", " ", item ).strip().rstrip( "." ) for item in items if item ]


def _form_pages( pages: list[ str ]) -> list[ str ]:
    return [ p for p in pages if re.search( r"CF-1040|GRR", p ) and re.search( r"\b1[3-9]\b", p )]


def _form_lines( pages: list[ str ]) -> dict[ str, str ]:
    lines: dict[ str, str ] = {}
    for page in _form_pages( pages )[ :1 ]:
        for raw in page.splitlines():
            match = re.match( r"^\s*(?:[A-Z ]{3,}\s+)?(1[0-9]|2[0-6]|[1-9])\s+([A-Z][A-Za-z(].{6,}?)\s*(?:\d+[a-e]?\s*)*$",
                              raw )
            if match and match.group( 1 ) not in lines:
                label = re.split( r"\s{2,}|\[|\|", match.group( 2 ))[ 0 ]
                lines[ match.group( 1 )] = _squash( label )[ :90 ]
    return dict( sorted( lines.items(), key=lambda kv: int( kv[ 0 ])))


def _sentences( pages: list[ str ], tax_year: int ) -> list[ str ]:
    form = set( _form_pages( pages ))
    text = " ".join( p for p in pages if p not in form )
    out: list[ str ] = []
    for sentence in re.split( r"(?<=[.!?])\s+", _squash( text )):
        if len( sentence ) < 25:
            continue
        normalized = re.sub( rf"\b{ tax_year + 1 }\b", "<NEXT YEAR>", re.sub( rf"\b{ tax_year }\b", "<TAX YEAR>", sentence ))
        out.append( normalized.lower())
    return sorted( set( out ))


def read_year( pages: list[ str ], tax_year: int, source: str, ocr_pages: list[ int ] | None = None ) -> YearFacts:
    text = "\n".join( pages )
    flat = _squash( text )
    facts = YearFacts( tax_year=tax_year, source=source, ocr_pages=ocr_pages or [])
    if m := re.search( r"(\d\d)M[Il1]-GRR-?\s*(?:\(R\)|R)?\s*-1040-1", flat ):
        facts.form_id = re.sub( r"[\s()]", "", m.group( 0 )).replace( "Ml", "MI" ).replace( "M1", "MI" )
    if m := re.search( r"Form (GR-1040R)", flat ):
        facts.title = m.group( 1 )
    elif "CF-1040" in flat:
        facts.title = "CF-1040"
    if m := re.search( r"resident rate \((\.\d+)\)", flat ):
        facts.resident_rate = Decimal( m.group( 1 ))
    if m := re.search( r"non-resident rate \((\.\d+)\)", flat ):
        facts.nonresident_rate = Decimal( m.group( 1 ))
    if m := re.search( r"multiply by \$?\s?(\d{3,4})", flat ):
        facts.exemption_amount = Decimal( m.group( 1 ))
    if m := re.search( r"(\d{4}) DUE\s+DATE:\s*APRIL 30th,\s*(\d{4})", flat, re.I ):
        facts.due_date = date( int( m.group( 2 )), 4, 30 )
    for page in _form_pages( pages )[ :1 ]:
        for raw in page.splitlines():
            if "Regular" in raw or "Reguiar" in raw:
                facts.exemption_boxes = [ b for b in _BOXES if b.lower() in raw.lower().replace( "reguiar", "regular" )]
                break
    facts.exempt_income = _numbered( _between( text, "does not tax the following types of income:",
                                               "ITEMS NOT DEDUCTIBLE" ))
    refused = _between( text, "following federal adjustments are not deductible on the Grand Rapids return:", "." )
    facts.not_deductible = [ _squash( part ) for part in re.split( r",\s*(?:and\s+)?|\s+and\s+", refused ) if part.strip()]
    facts.deductions = _numbered( _between( text, "Allowable deductions include:", "ADDRESS SCHEDULE" ))[ :8 ]
    facts.form_lines = _form_lines( pages )
    facts.form_text = _squash( " ".join( _form_pages( pages )[ :1 ]))
    facts.sentences = _sentences( pages, tax_year )
    return facts


def _ref( facts: YearFacts, note: str = "" ) -> SourceRef:
    return SourceRef( kind=SourceKind.OFFICIAL_FORM, where=facts.source, note=note )


def _show( value: object ) -> str:
    if value is None or value == [] or value == "":
        return "not stated"
    if isinstance( value, list ):
        return "; ".join( str( v ) for v in value )
    return str( value )


class FactsComparator:
    """ITaxYearComparator over two `YearFacts` dicts (before = 2024, after = 2025)."""

    def compare( self, before: dict, after: dict ) -> list[ FormDifference ]:
        a, b = YearFacts.model_validate( before ), YearFacts.model_validate( after )
        diffs: list[ FormDifference ] = []
        refs = [ _ref( a ), _ref( b )]

        def value( area: str, item: str, x: object, y: object, affects: bool, why_same: str = "" ) -> None:
            if x is None or y is None or x == [] or y == []:
                if x != y:
                    diffs.append( FormDifference( area=area, item=item, before=_show( x ), after=_show( y ),
                                                  kind=ChangeKind.UNCERTAIN, affects_barnes=affects, sources=refs,
                                                  explanation="One year's packet doesn't state this in readable "
                                                              "text, so no change is claimed." ))
                return
            if x != y:
                diffs.append( FormDifference( area=area, item=item, before=_show( x ), after=_show( y ),
                                              kind=ChangeKind.SUBSTANTIVE, affects_barnes=affects, sources=refs,
                                              explanation="The official text states a different value." ))
            elif why_same:
                diffs.append( FormDifference( area=area, item=item, before=_show( x ), after=_show( y ),
                                              kind=ChangeKind.UNCHANGED, affects_barnes=affects, sources=refs,
                                              explanation=why_same ))

        pct = lambda r: None if r is None else f"{( r * 100 ).normalize():f}%"
        usd = lambda d: None if d is None else f"${ d:,.0f}"
        value( "Rates", "Resident tax rate", pct( a.resident_rate ), pct( b.resident_rate ), True, "Unchanged." )
        value( "Rates", "Non-resident tax rate", pct( a.nonresident_rate ), pct( b.nonresident_rate ), False,
               "Unchanged." )
        value( "Exemptions", "Amount per exemption", usd( a.exemption_amount ), usd( b.exemption_amount ), True,
               "Unchanged." )
        value( "Exemptions", "Boxes that each count as an exemption", a.exemption_boxes, b.exemption_boxes, True,
               "Same boxes on both official blank forms." )
        value( "Income the city does not tax", "Exempt income list", a.exempt_income, b.exempt_income, True,
               "Same seven items, including Social Security and pensions." )
        value( "Deductions", "Federal adjustments the city refuses", a.not_deductible, b.not_deductible, True,
               "Same list: the self-employed health insurance deduction and one-half of SE tax stay disallowed." )
        value( "Deductions", "Allowable deductions", a.deductions, b.deductions, False, "Same list." )
        if a.due_date and b.due_date:
            moved = ( b.due_date.year - a.due_date.year == 1 and ( a.due_date.month, a.due_date.day )
                      == ( b.due_date.month, b.due_date.day ))
            diffs.append( FormDifference( area="Filing", item="Due date", before=str( a.due_date ),
                                          after=str( b.due_date ), affects_barnes=True, sources=refs,
                                          kind=ChangeKind.COSMETIC if moved else ChangeKind.SUBSTANTIVE,
                                          explanation=( "The same April 30 deadline, one year later." if moved
                                                        else "The deadline moved." )))
        same_form = a.title == b.title and a.form_id[ 2: ] == b.form_id[ 2: ]
        diffs.append( FormDifference( area="Packet", item="Form name / id", before=f"{ a.title } { a.form_id }".strip(),
                                      after=f"{ b.title } { b.form_id }".strip(), kind=ChangeKind.COSMETIC, sources=refs,
                                      explanation=( "Same packet and the same CF-1040 return layout; only the year "
                                                    "in the id moved." if same_form else
                                                    "The name or id changed; the return page is still the CF-1040 "
                                                    "layout." )))
        for number in sorted( set( a.form_lines ) | set( b.form_lines ), key=int ):
            x, y = a.form_lines.get( number ), b.form_lines.get( number )
            other = a if x is None else b
            label = y if x is None else x
            if ( x is None ) != ( y is None ) and _present( label, other.form_text ):
                diffs.append( FormDifference( area="Form lines", item=f"Line { number }", before=x or label,
                                              after=y or label, kind=ChangeKind.COSMETIC, sources=refs,
                                              explanation="Same line on both forms (OCR dropped the line number "
                                                          "on one page)." ))
            elif x is None or y is None:
                diffs.append( FormDifference( area="Form lines", item=f"Line { number }", before=_show( x ),
                                              after=_show( y ), kind=ChangeKind.UNCERTAIN, sources=refs,
                                              explanation="Read in one year only (OCR may have missed it); "
                                                          "check the PDFs before calling it a new or removed line." ))
            elif _words( x ) != _words( y ):
                diffs.append( FormDifference( area="Form lines", item=f"Line { number }", before=x, after=y,
                                              kind=ChangeKind.COSMETIC, sources=refs,
                                              explanation="Same line number; wording or OCR differs." ))
        gone = sorted( set( a.sentences ) - set( b.sentences ))
        new = sorted( set( b.sentences ) - set( a.sentences ))
        for sentence in new[ :25 ]:
            diffs.append( FormDifference( area="Instructions", item="New or reworded sentence", after=sentence,
                                          kind=ChangeKind.UNCERTAIN, sources=refs,
                                          explanation="Appears in 2025 only; read it before assuming a rule changed." ))
        for sentence in gone[ :25 ]:
            diffs.append( FormDifference( area="Instructions", item="Removed or reworded sentence", before=sentence,
                                          kind=ChangeKind.UNCERTAIN, sources=refs,
                                          explanation="Appears in 2024 only." ))
        return diffs


def _words( text: str ) -> set[ str ]:
    return { w for w in re.findall( r"[a-z]{3,}", text.lower())}


def _present( label: str, text: str ) -> bool:
    """Most of a label's words appear on the other form's page."""
    words = _words( label )
    return bool( words ) and len( words & _words( text )) / len( words ) >= 0.7
