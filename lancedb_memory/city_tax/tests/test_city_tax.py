"""Offline tests for Liz's city-tax domain. Run from lancedb_memory/:

    .venv/bin/python -m city_tax.tests.test_city_tax

No network, no OCR binary, no model. The figures below are made up for the
tests -- none is a real taxpayer value, and none is asserted to make a real
return pass.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from city_tax.calculator import ( CityFormFieldMapper, GrandRapidsResidentCalculator, parts_check, reconcile,
                                  verify )
from city_tax.compare import FactsComparator, read_year
from city_tax.desk import Intent, read_request, windows_to_wsl
from city_tax.documents import classify_folder, close_chain
from city_tax.federal import PlanPageFederalRepository
from city_tax.forms import classify_link, parse_forms_page
from city_tax.models import ( ChangeKind, CityTaxRules, DocumentClass, Figure, FigureStatus, FormKind, Residency,
                              ReturnStatus, ScanRequest, ScanState, SourceKind, SourceRef )
from city_tax.reports import review_report
from city_tax.rules import DocumentResidencyResolver, exemptions_for
from city_tax.scans import ScanDesk, dollar_amounts
from city_tax.state import LizWorkflow, WorkflowState
from city_tax.store import InMemoryWorkpapers, whole_dollars

ADWS = Path( __file__ ).resolve().parents[ 3 ] / "adws"


# -- helpers -------------------------------------------------------------------

def _packet( year: int, exemption: str = "600", extra: str = "" ) -> list[ str ]:
    instructions = (
        f"Returns are due on or before April 30th each year. {year} DUE DATE: APRIL 30th, {year + 1}. "
        "Grand Rapids does not tax the following types of income:\n1 Social Security, pensions and annuities.\n"
        "2. Proceeds of insurance where the taxpayer paid policy premiums.\nITEMS NOT DEDUCTIBLE ON THE GRAND "
        "RAPIDS RETURN the following federal adjustments are not deductible on the Grand\nRapids return: student "
        "loan interest, self-employed health insurance deduction, and one-half or self employment tax. Grand "
        "Rapids allowable deductions can be found on page 2. Multiply by the resident rate (.015) or the "
        f"non-resident rate (.0075) depending on your filing status. This sentence is the same every year {year}. "
        + extra )
    form = ( f"CF-1040 Grand Rapids Resident {str( year )[ 2: ]}MI-GRR-R-1040-1\n"
             "1a You Date of birth Regular 65 or over Blind\n"
             "2 Taxable interest\n4 Business income or (loss) attach federal Schedule C\n"
             "13 Total income (Add lines 1 through 11)\n"
             f"16 Exemptions - Enter number from line 1f in 16a, multiply by {exemption}, enter in 16b\n"
             "17 Total income subject to tax (Subtract line 16b from line 15)\n" )
    return [ instructions, form ]


def _rules() -> CityTaxRules:
    return CityTaxRules( tax_year=2025, resident_rate=Decimal( "0.015" ), nonresident_rate=Decimal( "0.0075" ),
                         exemption_amount=Decimal( "600" ), exemption_boxes=[ "Regular", "65 or over", "Blind" ],
                         due_date=date( 2026, 4, 30 ), exempt_income=[], not_deductible=[], allowable_deductions=[],
                         sources=[ SourceRef( kind=SourceKind.OFFICIAL_FORM, where="test" )])


def _figures( a: str = "1000.40", b: str = "2000.60", interest: str = "10.00" ) -> list[ Figure ]:
    ref = [ SourceRef( kind=SourceKind.WORKPAPER, where="test" )]
    return [ Figure( key="church_pay_robert", label="r", amount=Decimal( a ), status=FigureStatus.PROVISIONAL, sources=ref ),
             Figure( key="church_pay_rosemary", label="m", amount=Decimal( b ), status=FigureStatus.PROVISIONAL, sources=ref ),
             Figure( key="taxable_interest", label="i", amount=Decimal( interest ), status=FigureStatus.VERIFIED, sources=ref )]


# -- money, forms, documents ------------------------------------------------------

async def test_whole_dollars_rounds_half_up_like_the_form_says():
    assert whole_dollars( Decimal( "1375.245" )) == Decimal( "1375" )
    assert whole_dollars( Decimal( "10.50" )) == Decimal( "11" )
    assert whole_dollars( Decimal( "10.49" )) == Decimal( "10" )


async def test_form_links_are_classified_from_their_own_words():
    page = ( '<a href="/m/x/2025-resident-fillable-form-revised.pdf">2025 Resident Tax Return</a>'
             '<a href="/m/y/2025-resident-printable.pdf">2025 Resident Tax Return</a>'
             '<a href="/m/z/prior-res-common-form-grand-rapids.pdf">Prior Year Resident Tax Return</a>'
             '<a href="/m/s/res-spanish-common-form-grand-rapids-2025.pdf">2025 Resident Tax Return</a>' )
    forms = { f.key: f for f in parse_forms_page( page, "https://example.test/" )}
    assert forms[ "2025-resident-return-fillable" ].fillable
    assert forms[ "2025-resident-instructions-printable" ].kind is FormKind.INSTRUCTIONS
    assert forms[ "prior-resident-return-fillable" ].tax_year == 0           # the year is chosen on the form
    assert not any( "spanish" in f.url for f in forms.values())
    assert classify_link( "Power of Attorney Form", "/poa.pdf", "" ) is None


async def test_document_folders_are_classified_by_name():
    assert classify_folder( "CF-1040_grand_rapids_page_1" ) is DocumentClass.COMPLETED_TAXPAYER_RETURN
    assert classify_folder( "other_tax_payment_worksheet_CFWK_OTP" ) is DocumentClass.PAYMENT_OR_EXTENSION
    assert classify_folder( "three_year_state_tax_return_comparison_GR_COMP" ) is DocumentClass.CITY_SCHEDULE
    assert classify_folder( "form_1040-SR_front_4" ) is DocumentClass.FEDERAL_SUPPORTING


async def test_last_years_figures_are_kept_only_when_the_page_arithmetic_closes():
    page = "2 Taxable interest 50\n4 Business 9,950\n13 10,000\n16b 1,200\n17 8,800\n18b 132\n22 18\n25 18"
    sheet = "1. Estimated tax payments 150 .00\n8. Total 150"
    chain = close_chain( page, sheet )
    assert chain is not None
    assert ( chain[ "total" ], chain[ "exemptions" ], chain[ "tax" ], chain[ "refund" ]) == (
        Decimal( "10000" ), 2, Decimal( "132" ), Decimal( "18" ))
    assert close_chain( page.replace( "132", "150" ), sheet ) is None        # misread tax: nothing is trusted


# -- comparing years --------------------------------------------------------------

async def test_same_rules_in_both_years_is_no_substantive_change():
    a = read_year( _packet( 2024 ), 2024, "a" )
    b = read_year( _packet( 2025 ), 2025, "b" )
    diffs = FactsComparator().compare( a.model_dump( mode="json" ), b.model_dump( mode="json" ))
    assert not [ d for d in diffs if d.kind is ChangeKind.SUBSTANTIVE ]
    rate = next( d for d in diffs if d.item == "Resident tax rate" )
    assert rate.kind is ChangeKind.UNCHANGED and rate.after == "1.5%"
    due = next( d for d in diffs if d.item == "Due date" )
    assert due.kind is ChangeKind.COSMETIC                                     # one year later, same day
    assert not [ d for d in diffs if d.area == "Instructions" ]               # year-only wording isn't a change


async def test_a_changed_exemption_amount_is_substantive():
    a = read_year( _packet( 2024, "600" ), 2024, "a" )
    b = read_year( _packet( 2025, "700" ), 2025, "b" )
    diffs = FactsComparator().compare( a.model_dump( mode="json" ), b.model_dump( mode="json" ))
    changed = [ d for d in diffs if d.kind is ChangeKind.SUBSTANTIVE ]
    assert [ d.item for d in changed ] == [ "Amount per exemption" ]


async def test_a_value_one_year_does_not_state_is_uncertain_never_a_change():
    a = read_year( _packet( 2024 ), 2024, "a" )
    a.exemption_amount = None
    b = read_year( _packet( 2025 ), 2025, "b" )
    diffs = FactsComparator().compare( a.model_dump( mode="json" ), b.model_dump( mode="json" ))
    item = next( d for d in diffs if d.item == "Amount per exemption" )
    assert item.kind is ChangeKind.UNCERTAIN


async def test_a_new_sentence_is_flagged_for_reading_not_called_a_rule():
    a = read_year( _packet( 2024 ), 2024, "a" )
    b = read_year( _packet( 2025, extra="The other-city credit is only for residents of the city here." ), 2025, "b" )
    diffs = FactsComparator().compare( a.model_dump( mode="json" ), b.model_dump( mode="json" ))
    new = [ d for d in diffs if d.area == "Instructions" ]
    assert new and all( d.kind is ChangeKind.UNCERTAIN for d in new )


# -- calculating ------------------------------------------------------------------

async def test_missing_payments_leave_every_dependent_line_blank():
    payments = Figure( key="payments_2025", label="p" )
    draft = GrandRapidsResidentCalculator().calculate( _figures(), _rules(), Residency.RESIDENT, 4, payments )
    c = lambda n: draft.line( n ).column_c
    assert c( "4" ) == Decimal( "3001" )                                       # 1,000 + 2,001: each rounded alone
    assert c( "13" ) == Decimal( "3011" ) and c( "16b" ) == Decimal( "2400" ) and c( "17" ) == Decimal( "611" )
    assert c( "18b" ) == Decimal( "9" )                                        # 611 x 1.5% = 9.165
    assert c( "19b" ) is None and c( "19d" ) is None and c( "21" ) is None and c( "22" ) is None
    assert draft.status is ReturnStatus.NEEDS_DOCUMENTS
    assert all( check.passed for check in verify( draft, _rules()) if check.name != "payments known" )


async def test_settled_payments_give_owed_or_refund_and_provisional_pay_still_needs_review():
    paid = Figure( key="payments_2025", label="p", amount=Decimal( "20" ), status=FigureStatus.VERIFIED,
                   sources=[ SourceRef( kind=SourceKind.HUMAN_ANSWER, where="EG" )])
    draft = GrandRapidsResidentCalculator().calculate( _figures(), _rules(), Residency.RESIDENT, 4, paid )
    assert draft.line( "22" ).column_c == Decimal( "11" ) and draft.line( "21" ).column_c == Decimal( "0" )
    assert draft.status is ReturnStatus.NEEDS_REVIEW
    assert all( check.passed for check in verify( draft, _rules()))


async def test_only_a_resident_return_is_prepared():
    try:
        GrandRapidsResidentCalculator().calculate( _figures(), _rules(), Residency.UNKNOWN, 4,
                                                   Figure( key="payments_2025", label="p" ))
    except ValueError:
        return
    raise AssertionError( "an unsettled residency must not produce a return" )


async def test_church_pay_must_equal_its_parts():
    figures = _figures( "300.00", "100.00" ) + [
        Figure( key="salary_robert", label="", amount=Decimal( "250.00" )),
        Figure( key="offerings_robert", label="", amount=Decimal( "50.00" )),
        Figure( key="salary_rosemary", label="", amount=Decimal( "90.00" ))]
    checks = { c.name: c.passed for c in parts_check( figures )}
    assert checks == { "church pay (Robert) = sum of its parts": True, "church pay (Rosemary) = sum of its parts": False }


async def test_field_mapper_fills_figures_blanks_the_unknown_and_never_an_ssn_or_bank_field():
    draft = GrandRapidsResidentCalculator().calculate( _figures(), _rules(), Residency.RESIDENT, 4,
                                                       Figure( key="payments_2025", label="p" ))
    values = CityFormFieldMapper().fields( draft )
    assert values[ "t4c" ] == "3,001" and values[ "t18b" ] == "9"
    assert values[ "t19d" ] == "" and values[ "t25" ] == "" and values[ "t20c" ] == ""
    assert not [ k for k in values if re.search( r"SSN|Routing|Account", k, re.I )]


async def test_reconciliation_explains_a_change_as_income_not_law():
    paid = Figure( key="payments_2025", label="p", amount=Decimal( "0" ), status=FigureStatus.VERIFIED )
    draft = GrandRapidsResidentCalculator().calculate( _figures(), _rules(), Residency.RESIDENT, 4, paid )
    last = [ Figure( key="gr2024_total", label="", amount=Decimal( "5000" ), status=FigureStatus.VERIFIED )]
    tables = reconcile( draft, _figures(), last )
    assert "not from a change in the rules" in tables[ "why" ]
    total = next( r for r in tables[ "year_over_year" ] if r[ "item" ].startswith( "Total income" ))
    assert total[ "change" ] == Decimal( "-1989" )


# -- residency, exemptions, plan -------------------------------------------------------

async def test_residency_is_never_taken_from_an_address_alone():
    with tempfile.TemporaryDirectory() as raw:
        notes = Path( raw ) / "plan.md"
        notes.write_text( "They live at 1000 Example NW, Grand Rapids MI 49504." )
        alone = DocumentResidencyResolver( "no form code here", "x.jpg", ( notes, )).resolve( 2025 )
        assert alone[ 0 ] is Residency.UNKNOWN
        with_rate = DocumentResidencyResolver( "no form code here", "x.jpg", ( notes, ), prior_tax=Decimal( "15" ),
                                               prior_taxable=Decimal( "1000" ),
                                               prior_resident_rate=Decimal( "0.015" )).resolve( 2025 )
        assert with_rate[ 0 ] is Residency.RESIDENT


async def test_exemptions_count_regular_plus_sixty_five_or_over():
    births = { "a": date( 1941, 3, 3 ), "b": date( 1980, 1, 1 )}
    assert exemptions_for( births, 2025, [ "Regular", "65 or over" ]) == 3


async def test_the_plan_page_is_read_as_provisional_and_a_missing_figure_stays_missing():
    with tempfile.TemporaryDirectory() as raw:
        page = Path( raw ) / "plan.html"
        page.write_text( "<p>Enter this</p><td>$1,000.10</td><td>$2,000.20</td>"
                         "<p>Social Security — Robert / Rosemary $10.00 / $20.00</p>" )
        figures = { f.key: f for f in PlanPageFederalRepository( page, Path( raw ) / "none.md" ).figures( 2025 )}
        assert figures[ "church_pay_robert" ].amount == Decimal( "1000.10" )
        assert figures[ "church_pay_robert" ].status is FigureStatus.PROVISIONAL
        assert figures[ "salary_robert" ].status is FigureStatus.MISSING and figures[ "salary_robert" ].amount is None


# -- state, desk, scans ------------------------------------------------------------

async def test_the_state_machine_refuses_a_skip_and_resumes_a_pause():
    flow = LizWorkflow()
    try:
        flow.move( WorkflowState.AWAITING_REVIEW )
    except ValueError:
        pass
    else:
        raise AssertionError( "IDLE -> AWAITING_REVIEW must be refused" )
    flow.move( WorkflowState.CALCULATING )
    flow.move( WorkflowState.PAUSED )
    assert flow.resume() is WorkflowState.CALCULATING
    flow.fail( "calculate_city_tax", "boom" )
    assert flow.state is WorkflowState.FAILED and flow.failed_step == "calculate_city_tax"
    flow.checkpoint( "calculate_city_tax", [])
    assert flow.failed_step == ""


async def test_the_desk_reads_each_thing_eg_says():
    said = {
        "Liz, what changed between the 2024 and 2025 forms?": Intent.WHAT_CHANGED,
        "Show me that on the dashboard.": Intent.DASHBOARD,
        "Where did you get that income figure?": Intent.WHERE_FROM,
        "Why is that amount different from the federal return?": Intent.WHY_DIFFERENT,
        "Read the next supporting document.": Intent.SCANNED,
        "What information are we missing?": Intent.MISSING,
        "I've finished scanning it.": Intent.SCANNED,
        "Resume the tax preparation.": Intent.RESUME,
        "Show me the completed city tax form.": Intent.SHOW_FORM,
        "Liz, there weren't any city payments.": Intent.NONE_EXISTS,
        "The payment was $1,234.00": Intent.AMOUNT,
        "It's at C:\\Users\\me\\Downloads\\scan.pdf": Intent.SCAN_FILE,
        "Pause the city taxes for now.": Intent.PAUSE,
        "Tell me a joke.": Intent.UNKNOWN,
    }
    for sentence, intent in said.items():
        assert read_request( sentence ).intent is intent, sentence
    assert read_request( "The payment was $1,234.00" ).amount == "1,234.00"
    assert windows_to_wsl( "C:\\Users\\me\\a.pdf" ) == "/mnt/c/Users/me/a.pdf"


class _Todos:
    def __init__( self ) -> None:
        self.items: list[ str ] = []
        self.done: list[ int ] = []

    def add( self, text: str, who: str ):
        self.items.append( text )
        return SimpleNamespace( number=len( self.items ))

    def complete( self, number: int, who: str ):
        self.done.append( number )


class _Ledger:
    def __init__( self, entries ) -> None:
        self.entries = entries

    def recent( self, limit: int ):
        return self.entries


class _Ocr:
    def read( self, image: Path ) -> str:
        return "City of Grand Rapids estimated payment $123.45 received, balance $0.00"


async def test_a_scan_is_asked_for_once_found_after_the_request_and_only_offered():
    with tempfile.TemporaryDirectory() as raw:
        image = Path( raw ) / "scan.jpg"
        image.write_bytes( b"x" )
        asked = datetime.now().astimezone()
        before = SimpleNamespace( at=asked - timedelta( hours=1 ), source=str( Path( raw ) / "old.jpg" ))
        after = SimpleNamespace( at=asked + timedelta( minutes=1 ), source=str( image ))
        todos = _Todos()
        desk = ScanDesk( todos, _Ledger([ before ]), _Ocr())
        scan = desk.request( ScanRequest( key="k", document="d", reason="r", figure_key="f", requested_at=asked ))
        assert scan.todo_number == 1 and desk.request( scan ).todo_number == 1 and len( todos.items ) == 1
        assert desk.check( scan ).state is ScanState.WAITING                   # the old scan is not it
        desk = ScanDesk( todos, _Ledger([ after, before ]), _Ocr())
        got = desk.check( scan )
        assert got.state is ScanState.RECEIVED and got.candidates == [ "123.45", "0.00" ]
        closed = desk.close( got, ScanState.RECEIVED, "it was 123.45" )
        assert todos.done == [ 1 ] and closed.closed_at is not None
    assert dollar_amounts( "no money here" ) == []


# -- reports and the ADW -----------------------------------------------------------------

async def test_the_review_page_carries_no_ssn_bank_or_street_number():
    draft = GrandRapidsResidentCalculator().calculate( _figures(), _rules(), Residency.RESIDENT, 4,
                                                       Figure( key="payments_2025", label="p" ))
    page = review_report( draft, verify( draft, _rules()), reconcile( draft, _figures(), []), _figures(),
                          "status", "pdf", [], datetime.now())
    assert "Needs Documents" in page and "<em>blank</em>" in page
    assert not re.search( r"\d{3}-\d{2}-\d{4}|Routing|Gratiot", page )


async def test_the_answer_adw_records_none_closes_the_todo_and_clears_later_checkpoints():
    with tempfile.TemporaryDirectory() as raw:
        home, todos_home = Path( raw ) / "liz", Path( raw ) / "todos"
        env = { **os.environ, "LIZ_CITY_TAX_HOME": str( home ), "COMPANY_TODOS_HOME": str( todos_home )}
        sys.path.insert( 0, str( ADWS.parent / "lancedb_memory" ))
        os.environ[ "COMPANY_TODOS_HOME" ] = str( todos_home )
        from company_todos.store import JsonlTodoStore
        number = JsonlTodoStore().add( "Liz needs a scan: test", "liz" ).number
        papers = home / "work"
        papers.mkdir( parents=True )
        ( papers / "scans.json" ).write_text( json.dumps({ "gr_2025_payments": ScanRequest(
            key="gr_2025_payments", document="d", reason="r", figure_key="payments_2025",
            requested_at=datetime.now().astimezone(), todo_number=number ).model_dump( mode="json" )}))
        flow = LizWorkflow()
        flow.move( WorkflowState.READING_FINANCE )
        flow.move( WorkflowState.AWAITING_DOCUMENT_SCAN )
        flow.checkpoint( "calculate_city_tax", [])
        flow.checkpoint( "read_federal_figures", [])
        ( home / "state.json" ).write_text( flow.model_dump_json())
        line = Path( raw ) / "line.txt"
        done = subprocess.run([ sys.executable, str( ADWS / "adw_liz_ask.py" ), "--request",
                                "Liz, there weren't any city payments in 2025.", "--message-out", str( line )],
                              cwd=ADWS.parent, env=env, capture_output=True, text=True, timeout=300 )
        assert done.returncode == 0, done.stderr[ -800: ]
        assert line.read_text().startswith( "Recorded: 2025 Grand Rapids payments $0.00 (none existed)" )
        figure = json.loads(( papers / "payments_2025.json" ).read_text())
        assert figure[ "amount" ] == "0" and figure[ "sources" ][ 0 ][ "kind" ] == "human_answer"
        state = json.loads(( home / "state.json" ).read_text())
        assert "calculate_city_tax" not in state[ "checkpoints" ] and "read_federal_figures" in state[ "checkpoints" ]
        assert JsonlTodoStore().items()[ 0 ].state.value == "done"
        assert json.loads(( papers / "scans.json" ).read_text())[ "gr_2025_payments" ][ "state" ] == "answered"


if __name__ == "__main__":
    from tests.runner import main
    main( globals())
