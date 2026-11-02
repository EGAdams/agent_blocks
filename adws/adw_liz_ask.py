#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Ask -- one sentence to Liz about the city return, answered from her workpapers.

Her receptionist route sends the user's words here, with no model in the
path: `city_tax.desk.read_request` names the intent; questions are answered
from the saved workpapers (sources included); actions run the matching
small ADW (wait_for_scan, record_answer, record_review, prepare_city_taxes)
as a subprocess and repeat its line. An unrecognised sentence says so --
it never guesses an action.

    reads:  every Liz workpaper, read only (actions write through their own ADWs)
    writes: nothing itself

    lancedb_memory/.venv/bin/python adws/adw_liz_ask.py --request "what changed between the 2024 and 2025 forms?"
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from liz_adw import Kit, LizStep, run_step
from city_tax.desk import Intent, read_request, windows_to_wsl
from city_tax.state import WorkflowState, describe

STEP = LizStep( "ask", WorkflowState.IDLE, "Answer one request about the city return", checkpointed=False )
HERE = Path( __file__ ).resolve().parent


def sub_adw( script: str, args: list[ str ], timeout: float = 900 ) -> str:
    with tempfile.TemporaryDirectory() as scratch:
        line_file = Path( scratch ) / "line.txt"
        try:
            subprocess.run([ sys.executable, str( HERE / script ), *args, "--message-out", str( line_file )],
                           cwd=HERE.parent, capture_output=True, text=True, timeout=timeout )
        except subprocess.TimeoutExpired:
            return "That step is taking longer than I can wait on the line; it keeps running, ask me for status shortly."
        return line_file.read_text().strip() if line_file.exists() else "That step finished without saying anything."


def _money( value ) -> str:
    return "blank" if value is None else f"${ Decimal( str( value )):,.0f}"


def answer_what_changed( kit: Kit ) -> str:
    diffs = kit.papers.load( "form_differences" )
    if not diffs:
        return "I haven't compared the forms yet. Say \"prepare the city taxes\" and I'll start there."
    sub = [ d for d in diffs if d[ "kind" ] == "substantive" ]
    words = [ d for d in diffs if d[ "area" ] == "Instructions" ]
    page = ( kit.papers.load( "published_comparison" ) or {}).get( "title", "the comparison report" )
    if not sub:
        return ( "Nothing that sets the tax changed: the 2025 instructions keep the 1.5% resident rate, $600 per "
                 "exemption, the same untaxed income -- Social Security included -- and the same disallowed "
                 "deductions. The due date moved to April 30, 2026, and the wording changed in a few places, like "
                 f"saying the other-city credit is for residents only ({ len( words )} sentences flagged for a read). "
                 f"It's all on \"{ page }\" on the ADW Generated tab." )
    return ( "These changed in a way that matters: " + "; ".join( f"{ d[ 'item' ]} from { d[ 'before' ]} to { d[ 'after' ]}"
                                                               for d in sub ) + f". Details are on \"{ page }\"." )


def answer_dashboard( kit: Kit ) -> str:
    review, comparison = kit.papers.load( "published_review" ), kit.papers.load( "published_comparison" )
    shown = [ p for p in ( review, comparison ) if p ]
    if not shown:
        return "I haven't published anything yet."
    return ( "It's on the dashboard under ROL Finance, Reports, ADW Generated: "
             + " and ".join( f"\"{ p[ 'title' ]}\"" for p in shown ) + ", newest at the top." )


def answer_where_from( kit: Kit ) -> str:
    draft = kit.papers.load( "city_draft" )
    if not draft:
        return "I haven't calculated the return yet, so there's no figure to source."
    parts = []
    for row in draft[ "lines" ]:
        if row[ "line" ] in ( "2", "4" ):
            seen: list[ str ] = []
            for s in row[ "sources" ]:
                said = f"{ s.get( 'line' ) or s.get( 'note' )} ({ Path( s[ 'where' ]).name })"
                if said not in seen:
                    seen.append( said )
            where = "; ".join( seen[ :3 ])
            parts.append( f"line { row[ 'line' ]}, { row[ 'label' ].lower()}, { _money( row[ 'column_c' ])}: { where }" )
    return ( "From the family's tax plan, not a filed federal return -- that's on hold for a CPA/EA, so these are "
             "provisional. " + ". ".join( parts ) + "." )


def answer_why_different( kit: Kit ) -> str:
    rec = kit.papers.load( "reconciliation" )
    if not rec:
        return "I haven't reconciled the return yet."
    return ( "The city starts from the same church pay and interest as the federal return, but leaves out Social "
             "Security (the city doesn't tax it), the housing allowance (excluded federally too), and doesn't allow "
             "the self-employed health insurance deduction or half of SE tax. It also has no standard or senior "
             "deduction -- just $600 per exemption, four for Robert and Rosemary. " + rec.get( "why", "" ))


def answer_missing( kit: Kit ) -> str:
    items = kit.papers.load( "missing_items" )
    if items is None:
        return "I haven't checked yet. Say \"prepare the city taxes\" and I'll find out."
    scans = kit.papers.load( "scans" ) or {}
    out = []
    for item in items:
        scan = scans.get( item[ "key" ])
        tail = f" (to-do #{ scan[ 'todo_number' ]}, { scan[ 'state' ]})" if scan else ""
        out.append( f"{ item[ 'document' ]}{ tail }" )
    return ( "Still missing: " + "; ".join( out ) + "." ) if out else "Nothing is missing."


def show_form( kit: Kit ) -> str:
    fill = kit.papers.load( "pdf_fill" )
    if not fill:
        return "There's no filled form yet."
    draft = Path( fill[ "draft" ])
    try:
        windows = subprocess.run([ "wslpath", "-w", str( draft )], capture_output=True, text=True, timeout=10 ).stdout.strip()
        subprocess.Popen([ "cmd.exe", "/c", "start", "", windows ], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True )
        opened = "I've opened it on the office PC"
    except OSError:
        opened = "I couldn't open it from here"
    return ( f"{ opened }: { draft.name }. It's the city's own 2025 form, filled as a draft -- SSNs, bank numbers and "
             f"signatures are blank. It isn't on the dashboard because it carries names and the home address." )


def _answer( kit: Kit, handle ) -> None:
    request = read_request( kit.args.request )
    handle.log( intent=request.intent.value )
    i = request.intent
    if i is Intent.WHAT_CHANGED:
        kit.line = answer_what_changed( kit )
    elif i is Intent.DASHBOARD:
        kit.line = answer_dashboard( kit )
    elif i is Intent.WHERE_FROM:
        kit.line = answer_where_from( kit )
    elif i is Intent.WHY_DIFFERENT:
        kit.line = answer_why_different( kit )
    elif i is Intent.MISSING:
        kit.line = answer_missing( kit )
    elif i is Intent.STATUS:
        draft = kit.papers.load( "city_draft" ) or {}
        kit.line = describe( kit.flow ) + ( f" The draft's status is { draft[ 'status' ]}." if draft else "" )
    elif i is Intent.SHOW_FORM:
        kit.line = show_form( kit )
    elif i is Intent.SCANNED:
        kit.line = sub_adw( "adw_liz_wait_for_scan.py", [])
    elif i is Intent.SCAN_FILE:
        kit.line = sub_adw( "adw_liz_wait_for_scan.py", [ "--file", windows_to_wsl( request.path )])
    elif i is Intent.NONE_EXISTS:
        kit.line = sub_adw( "adw_liz_record_answer.py", [ "--none", "--said", request.said ])
    elif i is Intent.AMOUNT:
        kit.line = sub_adw( "adw_liz_record_answer.py", [ "--amount", request.amount, "--said", request.said ])
    elif i is Intent.PAUSE:
        kit.line = sub_adw( "adw_liz_record_review.py", [ "--pause", "--said", request.said ])
    elif i is Intent.APPROVE:
        kit.line = sub_adw( "adw_liz_record_review.py", [ "--approve", "--said", request.said ])
    elif i in ( Intent.PREPARE, Intent.RESUME ):
        kit.line = sub_adw( "adw_liz_prepare_city_taxes.py", [ "--draft-while-waiting" ] if "draft" in request.said.lower() else [])
    else:
        kit.line = ( "I'm not sure what you'd like me to do with the city return. I can say what changed between the "
                     "forms, what's missing, where a figure came from, why it differs from the federal return, show "
                     "the report or the form, or resume the preparation." )
    kit.flow = kit.store.load()      # a sub-ADW may have moved the workflow; never save over it


def add_args( parser ) -> None:
    parser.add_argument( "--request", default="", help="the user's sentence, unchanged" )


def phases( kit: Kit ):
    return [( "answer", "Read the request and answer it or run its ADW", _answer )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))
