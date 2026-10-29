"""Templated playbacks and the statement specialist, against a Chase-shaped page.

Pinned: one recorded month becomes a template only when a person says --yes,
and the recording itself is untouched; the template's month and year are
blanks, the statement's day a wildcard, and September's row-pinned ids are
gone; "get the October 2025 Chase statement" clicks October's row and never
September's; a month not posted yet fails and clicks nothing in its place;
and a template run with a blank left empty never opens the browser.
"""
from __future__ import annotations

from datetime import date

import pytest

from browser_workflows.models import LocatorCandidate as L, Period, RecordedAction
from browser_workflows.templating.period import CalendarPeriodVocabulary, WordPeriodParser

from .fakes import FakeBrowser, FakeElement
from .test_operations import PageKit, catalog

DOCS = "https://secure.chase.test/documents"


def recorded_september( kit ):
    """The shape of download_sept_2025_statement's last three steps."""
    return kit.repository().create( "download_sept_2025_statement", [
        RecordedAction( action_type="navigate", url=DOCS ),
        RecordedAction( action_type="click", locators=[ L( strategy="text", value="2025", tag='[role="option"]', source="text", rank=40 ),
                                                        L( strategy="css", value="#container-1-filterstyledselect-0", source="id" )]),
        RecordedAction( action_type="click", locators=[
            L( strategy="text", value="Sep 12, 2025 Opens menu to save document", tag="a", source="text", rank=40 ),
            L( strategy="css", value="#accountsTable-0-row3-cell3-downloadDocumentDropdown-icon", source="id" )]),
        RecordedAction( action_type="click", locators=[ L( strategy="text", value="Save as PDF 1 of 2 items", tag="a", source="text", rank=40 ),
                                                        L( strategy="css", value="#item-0-3-downloadPDFOption", source="id" )]),
    ])


def documents_page( months: dict[ str, str ]) -> tuple[ FakeBrowser, dict ]:
    """A year picker and one row per posted statement; a row's menu shows
    its own Save as PDF only once the row is clicked."""
    page = FakeBrowser( DOCS )
    els  = { "2025": FakeElement( "a", ( '[role="option"]', ), "2025" )}
    page.add( els[ "2025" ])
    for i, ( key, day ) in enumerate( months.items()):
        row  = FakeElement( "a", ( "a", f"#accountsTable-0-row{ i }-cell3-downloadDocumentDropdown-icon" ),
                            f"{ day } Opens menu to save document" )
        save = FakeElement( "a", ( "a", f"#item-0-{ i }-downloadPDFOption" ), "Save as PDF 1 of 2 items", displayed=False )
        row.on_click = lambda save=save: setattr( save, "displayed", True )
        els[ key ], els[ key + "_save" ] = row, save
        page.add( row, save )
    return page, els


@pytest.fixture
def chase( tmp_path ):
    page, els = documents_page({ "aug": "Aug 12, 2025", "sep": "Sep 12, 2025", "oct": "Oct 10, 2025" })
    kit = PageKit( tmp_path, page )
    recorded_september( kit )
    return kit, page, els


def template( kit ) -> None:
    out = catalog( kit )[ "parameterize_playback" ].run({ "name": "download_sept_2025_statement",
                                                          "new_name": "chase_statement_by_month", "yes": True })
    assert out.ok and out.data[ "saved" ], out.line


# -- months --------------------------------------------------------------------

@pytest.mark.parametrize( "text, period", [
    ( "get the October 2025 Chase statement", ( 2025, 10 )),
    ( "Chase statement for Oct '25", ( 2025, 10 )),
    ( "the October 25 statement", ( 2025, 10 )),
    ( "the Sept. 12, 2025 one", ( 2025, 9 )),
    ( "statement 2025-03", ( 2025, 3 )),
    ( "statement 11/2024", ( 2024, 11 )),
    ( "last month's Chase statement", ( 2026, 9 )),
    ( "the December statement", ( 2025, 12 )),        # no year: the latest December not in the future
    ( "the August statement", ( 2026, 8 )),
])
def test_periods_read_out_of_a_request( text, period ):
    found = WordPeriodParser( lambda: date( 2026, 10, 28 )).parse( text )
    assert found == Period( year=period[ 0 ], month=period[ 1 ])


def test_may_and_march_need_a_year_because_they_are_verbs_too():
    parser = WordPeriodParser( lambda: date( 2026, 10, 28 ))
    assert parser.parse( "you may get the Chase statement" ) is None
    assert parser.parse( "the May 2025 statement" ) == Period( year=2025, month=5 )


def test_vocabulary():
    v = CalendarPeriodVocabulary().values( Period( year=2025, month=9 ))
    assert v == { "year": "2025", "yy": "25", "month": "9", "month_2": "09", "month_name": "September", "month_abbr": "Sep" }


# -- parameterising -------------------------------------------------------------

def test_the_proposal_saves_nothing_and_the_recording_is_never_changed( chase ):
    kit, _, _ = chase
    before = kit.repository().by_name( "download_sept_2025_statement" )
    out    = catalog( kit )[ "parameterize_playback" ].run({ "name": "download_sept_2025_statement" })
    assert out.ok and not out.data[ "saved" ] and "--yes" in out.line
    assert kit.repository().by_name( "chase_statement_by_month" ) is None
    template( kit )
    assert kit.repository().by_name( "download_sept_2025_statement" ) == before


def test_month_and_year_become_blanks_and_septembers_row_ids_go( chase ):
    kit, _, _ = chase
    template( kit )
    steps = kit.repository().by_name( "chase_statement_by_month" ).steps
    assert [( c.strategy, c.value ) for c in steps[ 1 ].locators ] == [( "text", "{year}" )]
    assert [( c.strategy, c.value ) for c in steps[ 2 ].locators ] == [
        ( "text_pattern", "{month_abbr} *, {year} Opens menu to save document" )]
    assert [( c.strategy, c.value ) for c in steps[ 3 ].locators ] == [( "text", "Save as PDF 1 of 2 items" )]


# -- the statement specialist ---------------------------------------------------

def test_october_is_fetched_from_septembers_recording( chase ):
    kit, page, els = chase
    template( kit )
    out = catalog( kit )[ "statement_request" ].run({ "request": "get the October 2025 Chase statement", "actor": "toyota" })
    assert out.ok, out.line
    assert out.line.startswith( "Chase statement for October 2025:" ) and "month_abbr=Oct" in out.line
    assert els[ "oct" ].clicks == 1 and els[ "oct_save" ].clicks == 1
    assert els[ "sep" ].clicks == 0 and els[ "sep_save" ].clicks == 0
    assert kit.run_log().run( out.data[ "run_id" ]).actor == "toyota"


def test_a_month_not_posted_yet_fails_and_clicks_nothing_in_its_place( chase ):
    kit, _, els = chase
    template( kit )
    out = catalog( kit )[ "statement_request" ].run({ "request": "Chase statement for November 2025" })
    assert not out.ok and "not be posted yet" in out.line
    assert all( e.clicks == 0 for k, e in els.items() if k != "2025" )


def test_the_specialist_refuses_what_it_cannot_read( chase ):
    kit, _, _ = chase
    ops = catalog( kit )
    assert "which bank" in ops[ "statement_request" ].run({ "request": "get the October 2025 statement" }).line
    assert "which month" in ops[ "statement_request" ].run({ "request": "get a Chase statement" }).line
    no_template = ops[ "statement_request" ].run({ "request": "October 2025 Chase statement" })
    assert not no_template.ok and "adw_browser_parameterize_playback.py" in no_template.line
    assert ops[ "statement_request" ].run({ "request": "which banks can you do?" }).line.endswith( "Chase" )
    assert kit.fake.opened == 0


def test_a_template_with_a_blank_left_empty_never_opens_the_browser( chase ):
    kit, _, _ = chase
    template( kit )
    out = catalog( kit )[ "execute_playback" ].run({ "name": "chase_statement_by_month" })
    assert not out.ok and "needs year, month_abbr" in out.line and kit.fake.opened == 0
    out = catalog( kit )[ "execute_playback" ].run({ "name": "chase_statement_by_month", "period": "Aug 2025" })
    assert out.ok, out.line
