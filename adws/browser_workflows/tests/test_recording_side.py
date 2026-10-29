"""Storage, sensitivity and normalising: everything between a raw event and a
saved playback, with a real SQLite file and no browser.

The decisions pinned here are the ones that fail quietly: a secret that gets
stored, a keystroke that becomes a step, a step id that changes under an edit,
a "Log In" that needs approval or a "Pay" that does not.
"""
from __future__ import annotations

import pytest

from browser_workflows.errors import DuplicateName
from browser_workflows.models import LocatorCandidate, RawBrowserEvent, RecordedAction, RecordingSessionInfo, StepResult, StepDiagnostics
from browser_workflows.normalizing.consequences import KeywordConsequenceClassifier
from browser_workflows.normalizing.mapper import RawEventMapper
from browser_workflows.normalizing.pipeline import RulePipelineNormalizer, default_rules
from browser_workflows.normalizing.sensitivity import RuleFileSensitivityPolicy
from browser_workflows.storage.database import SqliteDatabase
from browser_workflows.storage.recordings import SqliteRecordingRepository
from browser_workflows.storage.runs import SqlitePlaybackRunLog
from browser_workflows.storage.sessions import SqliteRecordingSessionStore

SITE = "https://example.test"


def css( value: str, rank: int = 50, source: str = "css" ) -> LocatorCandidate:
    return LocatorCandidate( strategy="css", value=value, source=source, rank=rank )


def ev( kind: str, **kw ) -> RawBrowserEvent:
    kw.setdefault( "url", f"{ SITE }/form" )
    return RawBrowserEvent( kind=kind, **kw )


@pytest.fixture
def db( tmp_path ) -> SqliteDatabase:
    return SqliteDatabase( tmp_path / "click_workspace_sequences.db" )


@pytest.fixture
def normalizer() -> RulePipelineNormalizer:
    return RulePipelineNormalizer( default_rules( RawEventMapper(), RuleFileSensitivityPolicy(), KeywordConsequenceClassifier()))


# -- sensitivity -------------------------------------------------------------

@pytest.mark.parametrize( "input_type,hints", [
    ( "password", {}),
    ( "text", { "autocomplete": "one-time-code" }),
    ( "text", { "autocomplete": "cc-number" }),
    ( "text", { "name": "routingNumber" }),
    ( "text", { "label": "Social Security Number" }),
    ( "text", { "id": "userName" }),
    ( "text", { "placeholder": "PIN" }),
])
def test_secret_fields_are_recognised( input_type, hints ):
    assert RuleFileSensitivityPolicy().reason( input_type, hints )


@pytest.mark.parametrize( "hints", [{ "name": "q", "label": "Search" }, { "name": "shipping_notes" }, { "id": "memo" }])
def test_ordinary_fields_are_not_secret( hints ):
    assert RuleFileSensitivityPolicy().reason( "text", hints ) == ""


# -- consequence -------------------------------------------------------------

@pytest.mark.parametrize( "text,expected", [( "Pay now", True ), ( "Transfer funds", True ), ( "Place   Order", True ),
                                            ( "Delete", True ), ( "Log In", False ), ( "Payroll report", False ),
                                            ( "Next", False )])
def test_consequential_clicks( text, expected ):
    action = RecordedAction( action_type="click", metadata={ "text": text })
    assert bool( KeywordConsequenceClassifier().reason( action )) is expected


# -- normalising -------------------------------------------------------------

def test_background_recovery_preserves_existing_locators_and_does_not_guess_other_elements():
    mapper = RawEventMapper()
    targeted = ev( "click", tag="body", locators=[ css( "#page" )])
    assert mapper.map( targeted ).locators == targeted.locators
    assert mapper.map( ev( "click", tag="button" )).locators == []


def test_one_text_entry_per_field_and_focus_clicks_dropped( normalizer ):
    field = [ css( "#memo" )]
    actions = normalizer.normalize([
        ev( "navigation", url=f"{ SITE }/form", transition_type="typed" ),
        ev( "click", tag="input", input_type="text", locators=field ),
        ev( "text_committed", tag="input", input_type="text", value="first", locators=field, field_hints={ "name": "memo" }),
        ev( "text_committed", tag="input", input_type="text", value="second", locators=field, field_hints={ "name": "memo" }),
        ev( "click", tag="input", input_type="submit", text="Go", locators=[ css( "#go" )]),
    ])
    assert [( a.action_type, a.value ) for a in actions ] == [( "navigate", None ), ( "enter_text", "second" ), ( "click", None )]


def test_secret_value_never_survives_even_if_extension_sent_it( normalizer ):
    leaked = ev( "text_committed", tag="input", input_type="password", value="hunter2", locators=[ css( "#pw" )])
    actions = normalizer.normalize([ ev( "navigation", transition_type="typed" ), leaked ])
    entry = actions[ -1 ]
    assert entry.value is None and entry.value_is_secret
    assert "hunter2" not in entry.model_dump_json()


def test_page_caused_navigation_folds_into_previous_step( normalizer ):
    actions = normalizer.normalize([
        ev( "navigation", url=f"{ SITE }/a", transition_type="typed" ),
        ev( "click", tag="a", text="Next", locators=[ css( "#next" )]),
        ev( "navigation", url=f"{ SITE }/b?token=1", transition_type="link" ),
    ])
    assert [ a.action_type for a in actions ] == [ "navigate", "click" ]
    assert { ( w.kind, w.value ) for w in actions[ 1 ].wait_after } == {( "url_contains", f"{ SITE }/b" ), ( "navigation_complete", "" )}


def test_typed_navigation_is_a_step_and_recording_starts_on_a_page( normalizer ):
    actions = normalizer.normalize([
        ev( "click", tag="button", text="Hello", url=f"{ SITE }/a", locators=[ css( "#h" )]),
        ev( "navigation", url=f"{ SITE }/c", transition_type="typed" ),
    ])
    assert [( a.action_type, a.url ) for a in actions ][ 0 ] == ( "navigate", f"{ SITE }/a" )
    assert actions[ -1 ].action_type == "navigate" and actions[ -1 ].url == f"{ SITE }/c"


def test_pay_button_requires_approval_login_does_not( normalizer ):
    actions = normalizer.normalize([
        ev( "navigation", transition_type="typed" ),
        ev( "click", tag="button", text="Log In", locators=[ css( "#login" )]),
        ev( "click", tag="button", text="Pay $40.00", locators=[ css( "#pay" )]),
    ])
    assert [ a.requires_approval for a in actions ] == [ False, False, True ]


def test_checkbox_click_becomes_one_set_checked( normalizer ):
    box = [ css( "#agree" )]
    actions = normalizer.normalize([
        ev( "navigation", transition_type="typed" ),
        ev( "click", tag="input", input_type="checkbox", locators=box ),
        ev( "check", tag="input", input_type="checkbox", checked=True, locators=box ),
    ])
    assert [( a.action_type, a.value ) for a in actions ] == [( "navigate", None ), ( "set_checked", "true" )]


# -- storage -----------------------------------------------------------------

def test_recording_round_trip_keeps_locator_order_and_drops_secret_values( db ):
    repo = SqliteRecordingRepository( db )
    saved = repo.create( "Open Form", [
        RecordedAction( action_type="navigate", url=f"{ SITE }/form" ),
        RecordedAction( action_type="enter_text", value="should not be stored", value_is_secret=True,
                        locators=[ css( "#pw", 90 ), css( "[name=pw]", 20, "name" )]),
    ])
    loaded = repo.by_name( "open form" )
    assert loaded is not None and loaded.id == saved.id and loaded.start_url == f"{ SITE }/form"
    assert [ l.value for l in loaded.steps[ 1 ].locators ] == [ "[name=pw]", "#pw" ]
    assert loaded.steps[ 1 ].value is None
    with db.connect() as conn:
        assert "should not be stored" not in str([ tuple( r ) for r in conn.execute( "SELECT * FROM recording_steps" )])


def test_names_are_unique_ignoring_case( db ):
    repo = SqliteRecordingRepository( db )
    repo.create( "Daily", [ RecordedAction( action_type="navigate", url=SITE )])
    with pytest.raises( DuplicateName ):
        repo.create( "DAILY", [])


def test_replace_steps_keeps_ids_of_surviving_steps( db ):
    repo = SqliteRecordingRepository( db )
    rec = repo.create( "R", [ RecordedAction( action_type="navigate", url=SITE ),
                              RecordedAction( action_type="click", locators=[ css( "#a" )]),
                              RecordedAction( action_type="click", locators=[ css( "#b" )])])
    first, a, b = rec.steps
    new = repo.replace_steps( rec.id, [ first, b, RecordedAction( action_type="click", locators=[ css( "#c" )])])
    assert [ s.id for s in new.steps[ :2 ]] == [ first.id, b.id ]
    assert [ s.position for s in new.steps ] == [ 1, 2, 3 ]
    assert a.id not in { s.id for s in new.steps }


def test_sessions_stage_events_and_reopen_latest( db ):
    store = SqliteRecordingSessionStore( db )
    store.open( RecordingSessionInfo( id="s1", state="recording", start_url=SITE ))
    store.append( "s1", ev( "click", locators=[ css( "#a" )]))
    store.append( "s1", ev( "click", locators=[ css( "#b" )]))
    assert [ e.locators[ 0 ].value for e in store.events( "s1" )] == [ "#a", "#b" ]
    info = store.latest_open()
    assert info is not None and info.id == "s1" and info.event_count == 2


def test_run_log_keeps_each_step_and_its_diagnostics( db ):
    rec = SqliteRecordingRepository( db ).create( "R", [ RecordedAction( action_type="navigate", url=SITE )])
    log = SqlitePlaybackRunLog( db )
    run_id = log.start( rec.id, "menu" )
    log.record( StepResult( run_id=run_id, step_id=rec.steps[ 0 ].id, position=1, action_type="navigate", status="failed",
                            diagnostics=StepDiagnostics( url=SITE, error="timeout" )))
    log.finish( run_id, "failed", "step 1 failed" )
    run = log.run( run_id )
    assert run is not None and run.status == "failed" and run.name == "R"
    assert run.results[ 0 ].diagnostics.error == "timeout"
    assert [ r.id for r in log.recent( recording_id=rec.id )] == [ run_id ]
