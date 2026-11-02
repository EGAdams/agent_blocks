"""The two review pages Liz publishes to ADW Generated.

Plain, self-contained HTML (no scripts, fonts or stylesheets to fetch) so it
opens the same on a phone. Written for someone reviewing taxes, not code.
Never printed: SSNs, bank numbers, street address -- the review needs none.
"""
from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal

from .models import ChangeKind, CheckResult, CityReturnDraft, Figure, FormDifference, OfficialForm

_STYLE = """
:root { --bg:#fff; --fg:#1d2330; --muted:#5d6676; --line:#dfe3ea; --head:#eef1f6; --accent:#2f5fa7;
        --sub:#fde8e8; --subfg:#8a1c1c; --cos:#e8f4ea; --cosfg:#1d5e2c; --unc:#fff5d6; --uncfg:#7a5a00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171d; --fg:#e4e8ef; --muted:#9aa3b2; --line:#2c323d; --head:#222833; --accent:#8db4f0;
          --sub:#4a1d1d; --subfg:#ffc9c9; --cos:#183a22; --cosfg:#bff0cb; --unc:#40350f; --uncfg:#ffe79a; } }
* { box-sizing:border-box; }
body { margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
       font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1000px; margin:0 auto; }
h1 { font-size:1.45rem; margin:0 0 4px; } h2 { font-size:1.15rem; margin:28px 0 8px; }
.meta { color:var(--muted); font-size:.9rem; }
.badge { display:inline-block; padding:2px 10px; border-radius:999px; font-weight:700; font-size:.85rem; }
.SUBSTANTIVE,.fail { background:var(--sub); color:var(--subfg); }
.COSMETIC,.UNCHANGED,.pass { background:var(--cos); color:var(--cosfg); }
.UNCERTAIN,.warn { background:var(--unc); color:var(--uncfg); }
.tablewrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; margin:8px 0; font-size:.92rem; }
th,td { border:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { background:var(--head); } td.n { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
a { color:var(--accent); } ul { padding-left:20px; } .note { color:var(--muted); font-size:.88rem; }
.box { border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:10px 0; }
"""


def _e( value: object ) -> str:
    return html.escape( "" if value is None else str( value ))


def _m( value: object ) -> str:
    if value is None or value == "":
        return "<em>blank</em>"
    try:
        return f"${ Decimal( str( value )):,.0f}"
    except Exception:  # noqa: BLE001 -- display only
        return _e( value )


def _page( title: str, body: str ) -> str:
    return ( f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
             f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{ _e( title )}</title>'
             f"<style>{ _STYLE }</style></head><body><main>{ body }</main></body></html>\n" )


def comparison_report( diffs: list[ FormDifference ], forms: list[ OfficialForm ], notes: list[ str ],
                       questions: list[ str ], generated: datetime ) -> str:
    sub = [ d for d in diffs if d.kind is ChangeKind.SUBSTANTIVE ]
    cos = [ d for d in diffs if d.kind in ( ChangeKind.COSMETIC, ChangeKind.UNCHANGED )]
    same = sum( 1 for d in diffs if d.kind is ChangeKind.UNCHANGED )
    unc = [ d for d in diffs if d.kind is ChangeKind.UNCERTAIN ]
    summary = ( f"<p><b>{ len( sub )}</b> substantive change(s) &middot; <b>{ same }</b> rule(s) checked and "
                f"unchanged &middot; <b>{ len( cos ) - same }</b> cosmetic change(s) &middot; <b>{ len( unc )}</b> "
                f"item(s) for a person to read. A change in wording or layout is not reported as a change in the "
                f"tax law.</p>" )
    if not sub:
        summary += ( "<div class='box'><b>Bottom line:</b> nothing that sets the tax changed. The official "
                     "2025 resident instructions state the same rules as 2024's -- see the rates, exemption, "
                     "exempt-income and deduction rows below -- and the return is the same GR-1040R packet with "
                     "the CF-1040 page layout. The deadline moved forward one year, as it does every year.</div>" )
    rows = "".join(
        f"<tr><td>{ _e( d.area )}</td><td>{ _e( d.item )}</td><td>{ _e( d.before ) or '&mdash;' }</td>"
        f"<td>{ _e( d.after ) or '&mdash;' }</td><td><span class='badge { d.kind.name }'>{ d.kind.value }</span></td>"
        f"<td>{ _e( d.explanation )}{ ' <b>(affects Robert &amp; Rosemary)</b>' if d.affects_barnes else '' }</td></tr>"
        for d in sub + cos if d.area != "Instructions" )
    sentences = "".join(
        f"<tr><td>{ _e( d.item )}</td><td>{ _e( d.before or d.after )}</td></tr>" for d in unc )
    links = "".join(
        f"<li><a href='{ _e( f.url )}'>{ _e( f.title )}</a> &mdash; tax year { f.tax_year or 'prior (generic)' }, "
        f"{ _e( f.kind.value )}{ ', fillable' if f.fillable else '' }"
        f"{ ' &mdash; <b>Internet Archive copy</b> of ' + _e( f.listed_on ) if f.archived else '' }"
        f"{ ', retrieved ' + f.retrieved_at.strftime( '%Y-%m-%d' ) if f.retrieved_at else '' }</li>"
        for f in forms )
    barnes = [ d for d in sub + cos if d.affects_barnes ]
    affects = "".join( f"<li><b>{ _e( d.item )}</b>: { _e( d.after )} &mdash; { _e( d.explanation )}</li>" for d in barnes )
    body = ( f"<h1>Grand Rapids City Taxes &mdash; 2024 vs. 2025 Form Comparison</h1>"
             f"<p class='meta'>Prepared by Liz (city tax ADWs) &middot; { generated:%Y-%m-%d %H:%M} &middot; "
             f"official resident forms and instructions only</p>{ summary }"
             f"<h2>Side by side</h2><div class='tablewrap'><table><tr><th>Area</th><th>Item</th><th>2024</th>"
             f"<th>2025</th><th>Kind</th><th>What it means</th></tr>{ rows }</table></div>"
             f"<h2>What may affect Robert and Rosemary</h2><ul>{ affects or '<li>Nothing beyond the items above.</li>' }</ul>"
             f"<h2>Notes</h2><ul>{ ''.join( f'<li>{ _e( n )}</li>' for n in notes )}</ul>"
             f"<h2>Unresolved questions</h2><ul>{ ''.join( f'<li>{ _e( q )}</li>' for q in questions ) or '<li>None.</li>' }</ul>"
             f"<h2>Sentences for a person to read</h2><p class='note'>Instruction sentences found in only one "
             f"year's packet. Most are the same text broken across columns differently, or OCR noise; none is "
             f"treated as a rule change.</p><div class='tablewrap'><table><tr><th>Which</th><th>Text</th></tr>"
             f"{ sentences or '<tr><td colspan=2>None.</td></tr>' }</table></div>"
             f"<h2>Official sources</h2><ul>{ links }</ul>" )
    return _page( "GR 2024 vs 2025 Forms", body )


def review_report( draft: CityReturnDraft, checks: list[ CheckResult ], tables: dict, federal: list[ Figure ],
                   federal_status: str, pdf_note: str, validations: list[ CheckResult ], generated: datetime ) -> str:
    line = lambda n: draft.line( n )
    tax, paid = line( "18b" ).column_c, line( "19d" ).column_c if line( "19d" ) else None
    owed = line( "21" ).column_c if line( "21" ) else None
    over = line( "22" ).column_c if line( "22" ) else None
    result = ( f"owes { _m( owed )}" if owed else f"refund { _m( over )}" if over else "balance waits on line 19b" )
    summary = ( f"<div class='box'><b>Status: <span class='badge warn'>{ _e( draft.status.value )}</span></b><br>"
                f"Grand Rapids taxable income { _m( line( '17' ).column_c )} &middot; city tax { _m( tax )} &middot; "
                f"payments { _m( paid )} &middot; { result }.<br><span class='note'>{ _e( federal_status )}</span></div>" )
    lines = "".join(
        f"<tr><td>{ _e( l.line )}</td><td>{ _e( l.label )}</td><td class='n'>{ _m( l.column_a ) if l.column_a is not None else '' }</td>"
        f"<td class='n'>{ _m( l.column_b ) if l.column_b is not None else '' }</td><td class='n'>"
        f"{ ( _e( f'{ l.column_c:.0f}' ) if l.line == '16a' else _m( l.column_c )) if l.column_c is not None else '<em>blank</em>' }</td>"
        f"<td>{ _e( l.explanation )}<br><span class='note'>{ '; '.join( _e( s.line or s.note or s.kind.value ) for s in l.sources[ :3 ])}</span></td></tr>"
        for l in sorted( draft.lines, key=lambda r: ( int( ''.join( ch for ch in r.line if ch.isdigit())), r.line )))
    fed = "".join(
        f"<tr><td>{ _e( r[ 'item' ])}</td><td class='n'>{ _m( r[ 'federal' ])}</td><td>{ _e( r[ 'treatment' ])}</td>"
        f"<td>{ _e( r[ 'status' ])}</td></tr>" for r in tables[ "federal_to_city" ])
    yoy = "".join(
        f"<tr><td>{ _e( r[ 'item' ])}</td><td class='n'>{ _m( r[ '2024' ])}</td><td class='n'>{ _m( r[ '2025' ])}</td>"
        f"<td class='n'>{ _m( r[ 'change' ]) if r[ 'change' ] is not None else '' }</td></tr>" for r in tables[ "year_over_year" ])
    check_rows = "".join(
        f"<tr><td>{ _e( c.name )}</td><td><span class='badge { 'pass' if c.passed else 'fail' }'>"
        f"{ 'pass' if c.passed else 'open' }</span></td><td>{ _e( c.detail )}</td></tr>" for c in checks + validations )
    pieces = "".join( f"<li>{ _e( f.label )}: { _m( f.amount )} <span class='note'>({ _e( f.status.value )})</span></li>"
                      for f in federal if "inside church pay" in f.label )
    body = ( f"<h1>Grand Rapids 2025 &mdash; Completed Draft and Federal Reconciliation</h1>"
             f"<p class='meta'>Prepared by Liz &middot; { generated:%Y-%m-%d %H:%M} &middot; Robert &amp; Rosemary "
             f"Barnes, married filing jointly, Grand Rapids residents &middot; <b>not filed, not paid</b></p>{ summary }"
             f"<h2>The draft, line by line</h2><div class='tablewrap'><table><tr><th>Line</th><th>What</th><th>A: Federal</th>"
             f"<th>B: Excluded</th><th>C: Taxable</th><th>Why / source</th></tr>{ lines }</table></div>"
             f"<h2>Federal figures and how the city treats them</h2><div class='tablewrap'><table><tr><th>Item</th>"
             f"<th>Federal</th><th>City</th><th>Status</th></tr>{ fed }</table></div>"
             f"<p class='note'>Church pay is made up of (each already inside the totals above, never added twice):</p>"
             f"<ul>{ pieces }</ul>"
             f"<h2>2024 city return vs. this draft</h2><div class='tablewrap'><table><tr><th>Line</th><th>2024 (filed)</th>"
             f"<th>2025 (draft)</th><th>Change</th></tr>{ yoy }</table></div>"
             f"<p class='note'>{ _e( tables.get( 'why', '' ))}</p>"
             f"<h2>Checks</h2><div class='tablewrap'><table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>"
             f"{ check_rows }</table></div>"
             f"<h2>Assumptions</h2><ul>{ ''.join( f'<li>{ _e( a )}</li>' for a in draft.assumptions )}</ul>"
             f"<h2>Outstanding questions</h2><ul>{ ''.join( f'<li>{ _e( q )}</li>' for q in draft.open_questions ) or '<li>None.</li>' }</ul>"
             f"<h2>The draft PDF</h2><p>{ _e( pdf_note )}</p>" )
    return _page( "GR 2025 Draft Review", body )
