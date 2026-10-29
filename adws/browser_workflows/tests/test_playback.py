"""Playback against a scripted page.

Pinned: locators are tried best-first and an ambiguous or hidden match is
refused rather than guessed; waits poll instead of sleeping; a consequential
step needs a person's yes; a secret is asked for, typed, and never kept; a
run can be stopped, paused and resumed; a broken locator gets a proposed,
verified repair that changes nothing until a person applies it.
"""
from __future__ import annotations

import pytest

from browser_workflows.editing.editor import RepositoryRecordingEditor
from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.kit import WorkflowKit
from browser_workflows.models import LocatorCandidate as L, RecordedAction, RecordingSummary, WaitCondition
from browser_workflows.playback.conditions import PollingConditionWaiter, default_condition_checks
from browser_workflows.playback.control import MemoryPlaybackControl
from browser_workflows.playback.executors import RetryingExecutor
from browser_workflows.playback.gates import ConsoleSecretSource, DenyApprovalGate, NoSecretSource, PreApprovedGate
from browser_workflows.playback.lease import FixedBrowserLease
from browser_workflows.playback.locating import PollingElementWaiter, RankedElementResolver, default_strategy_registry
from browser_workflows.presenting.matcher import WordPlaybackMatcher

from .fakes import FakeBrowser, FakeClock, FakeElement, ScriptedConsole

URL = "https://bank.test/pay"


def resolver() -> RankedElementResolver:
    return RankedElementResolver( default_strategy_registry())


# -- locating ----------------------------------------------------------------

def test_best_ranked_unique_visible_enabled_match_wins():
    page = FakeBrowser().add( FakeElement( "button", ( ".btn", ), "Pay" ), FakeElement( "button", ( ".btn", ), "Cancel" ),
                              FakeElement( "button", ( "#pay", ), "Pay", attrs={ "id": "pay" }))
    element, res = resolver().resolve( page, [ L( strategy="css", value=".btn", rank=10 ),
                                               L( strategy="css", value="#pay", rank=50 )])
    assert element.attrs[ "id" ] == "pay"
    assert [ a.outcome for a in res.attempts ] == [ "ambiguous", "found" ] and res.winner.endswith( "#pay" )


def test_hidden_disabled_and_broken_selectors_are_reported_not_used():
    page = FakeBrowser().add( FakeElement( "button", ( "#a", ), displayed=False ),
                              FakeElement( "button", ( "#b", ), enabled=False ))
    element, res = resolver().resolve( page, [ L( strategy="css", value="#a", rank=1 ), L( strategy="css", value="#b", rank=2 ),
                                               L( strategy="css", value="!!bad", rank=3 ), L( strategy="css", value="#c", rank=4 )])
    assert element is None and not res.found
    assert [ a.outcome for a in res.attempts ] == [ "hidden", "disabled", "error", "none" ]


def test_text_and_label_strategies():
    page = FakeBrowser().add( FakeElement( "a", text="  Log   In " ), FakeElement( "div", text="Welcome" ),
                              FakeElement( "label", text="Memo", attrs={ "for": "m" }),
                              FakeElement( "input", attrs={ "id": "m" }))
    found, _ = resolver().resolve( page, [ L( strategy="text", value="log in" )])
    assert found.tag_name == "a"
    found, _ = resolver().resolve( page, [ L( strategy="label", value="Memo" )])
    assert found.attrs[ "id" ] == "m"


@pytest.mark.parametrize( "dimension", [ "width", "height" ])
def test_zero_size_matches_are_skipped_for_a_recorded_fallback( dimension ):
    icon = FakeElement( "a", ( "#icon", ))
    icon.rect[ dimension ] = 0
    link = FakeElement( "a", ( "#link", ))
    page = FakeBrowser().add( icon, link )
    element, res = resolver().resolve( page, [ L( strategy="css", value="#icon", rank=10 ),
                                               L( strategy="css", value="#link", rank=50 )])
    assert element is link
    assert [ a.outcome for a in res.attempts ] == [ "zero_size", "found" ]


def test_text_match_uses_the_sized_link_instead_of_its_zero_size_icon():
    caption = "Sep 12, 2025 Opens menu to save document"
    icon = FakeElement( "a", text=caption )
    icon.rect = { "width": 0.0, "height": 0.0 }
    link = FakeElement( "a", text=caption )
    page = FakeBrowser().add( icon, link )
    element, res = resolver().resolve( page, [ L( strategy="text", value=caption )])
    assert element is link and res.found
    page.add( FakeElement( "a", text=caption ))
    element, res = resolver().resolve( page, [ L( strategy="text", value=caption )])
    assert element is None and res.attempts[ 0 ].outcome == "ambiguous"


def test_element_waiter_waits_for_a_nonzero_box():
    clock = FakeClock()
    late = FakeElement( "a", ( "#late", ))
    late.rect[ "width" ] = 0
    page = FakeBrowser().add( late )
    original = page.find

    def find( css ):
        if page.find_calls >= 3:
            late.rect[ "width" ] = 20
        return original( css )
    page.find = find
    element, _ = PollingElementWaiter( resolver(), clock ).wait_for( page, [ L( strategy="css", value="#late" )], 5 )
    assert element is late and 0 < clock.slept < 5


def test_element_waiter_polls_until_the_element_appears():
    clock = FakeClock()
    late  = FakeElement( "button", ( "#go", ), displayed=False )
    page  = FakeBrowser().add( late )
    original = page.find

    def find( css ):
        if page.find_calls >= 3:
            late.displayed = True
        return original( css )
    page.find = find
    element, _ = PollingElementWaiter( resolver(), clock ).wait_for( page, [ L( strategy="css", value="#go" )], 5 )
    assert element is late and 0 < clock.slept < 5


def test_condition_waiter_reports_what_did_not_happen():
    clock = FakeClock()
    page  = FakeBrowser( "https://bank.test/login" )
    problem = PollingConditionWaiter( default_condition_checks(), clock ).wait(
        page, [ WaitCondition( kind="url_contains", value="/home", timeout_seconds=2 )])
    assert "url_contains" in problem and clock.slept >= 2
    assert PollingConditionWaiter( default_condition_checks(), clock ).wait(
        page, [ WaitCondition( kind="url_contains", value="/login" )]) == ""


def test_retry_only_when_the_browser_says_the_click_did_not_happen():
    class ElementClickInterceptedException( Exception ):
        pass

    class Flaky:
        calls = 0

        def execute( self, browser, step, element ):
            self.calls += 1
            if self.calls < 3:
                raise ElementClickInterceptedException( "covered" )
            return "clicked"

    class Broken( Flaky ):
        def execute( self, browser, step, element ):
            self.calls += 1
            raise RuntimeError( "stale" )
    flaky, broken = Flaky(), Broken()
    assert RetryingExecutor( flaky, FakeClock()).execute( None, None, None ) == "clicked" and flaky.calls == 3
    with pytest.raises( RuntimeError ):
        RetryingExecutor( broken, FakeClock()).execute( None, None, None )
    assert broken.calls == 1


# -- whole runs --------------------------------------------------------------

def pay_bill( kit: WorkflowKit ):
    return kit.repository().create( "Pay Bill", [
        RecordedAction( action_type="navigate", url=URL ),
        RecordedAction( action_type="enter_text", value="Rent", locators=[ L( strategy="css", value="#memo" )]),
        RecordedAction( action_type="enter_text", value_is_secret=True, locators=[ L( strategy="css", value="#pin" )],
                        metadata={ "sensitive_reason": "a PIN" }),
        RecordedAction( action_type="click", requires_approval=True, metadata={ "text": "Pay now", "tag": "button" },
                        locators=[ L( strategy="css", value="#pay", rank=50 ), L( strategy="text", value="Pay now", rank=40 )],
                        wait_after=[ WaitCondition( kind="url_contains", value="/done" )]),
    ])


def bank_page() -> tuple[ FakeBrowser, dict ]:
    page = FakeBrowser()
    els  = { "memo": FakeElement( "input", ( "#memo", )), "pin": FakeElement( "input", ( "#pin", ), attrs={ "type": "password" }),
             "pay": FakeElement( "button", ( "#pay", ), "Pay now" )}
    els[ "pay" ].on_click = lambda: setattr( page, "current_url", "https://bank.test/done" )
    page.add( *els.values())
    return page, els


def run( kit, page, approvals, secrets, control=None, from_position=1, console=None ):
    executor = kit.playback_executor( FixedBrowserLease( page ), approvals, secrets, clock=FakeClock())
    if control is not None:
        executor._control = control
    return executor.execute( kit.repository().by_name( "Pay Bill" ), "test", from_position )


@pytest.fixture
def kit( tmp_path ) -> WorkflowKit:
    return WorkflowKit( db_path=tmp_path / "db.sqlite", log_path=tmp_path / "logs" / "recorder.jsonl" )


def test_complete_run_asks_for_the_secret_and_never_keeps_it( kit, tmp_path ):
    pay_bill( kit )
    page, els = bank_page()
    run_id, state, message = run( kit, page, PreApprovedGate({ 4 }), ConsoleSecretSource( ScriptedConsole( secrets=[ "4321" ])))
    assert state == "completed", message
    assert page.visits == [ URL ] and els[ "memo" ].typed == [ "Rent" ] and els[ "pin" ].typed == [ "4321" ]
    assert els[ "pay" ].clicks == 1
    history = kit.run_log().run( run_id )
    assert history.status == "completed" and [ r.status for r in history.results ] == [ "ok" ] * 4
    assert history.results[ 3 ].strategy.endswith( "Pay now" )         # text (rank 40) before css (rank 50)
    for f in ( tmp_path / "db.sqlite", tmp_path / "logs" / "playback.jsonl" ):
        assert b"4321" not in f.read_bytes()


def test_consequential_step_is_blocked_without_approval( kit ):
    pay_bill( kit )
    page, els = bank_page()
    _, state, message = run( kit, page, DenyApprovalGate(), ConsoleSecretSource( ScriptedConsole( secrets=[ "1" ])))
    assert state == "blocked" and "--from-step 4" in message and els[ "pay" ].clicks == 0


def test_missing_secret_blocks_before_anything_is_typed( kit ):
    pay_bill( kit )
    page, els = bank_page()
    _, state, message = run( kit, page, PreApprovedGate({ 4 }), NoSecretSource())
    assert state == "blocked" and "step 3" in message and els[ "pin" ].typed == []


def test_stop_and_resume_from_a_step( kit ):
    pay_bill( kit )
    page, els = bank_page()
    control = MemoryPlaybackControl()
    control.set( 1, "stop" )
    _, state, message = run( kit, page, PreApprovedGate({ 4 }), NoSecretSource(), control=control )
    assert state == "stopped" and page.visits == []
    _, state, _ = run( kit, page, PreApprovedGate({ 4 }), NoSecretSource(), from_position=4 )
    assert state == "completed" and page.visits == [] and els[ "pay" ].clicks == 1


def test_pause_then_resume( kit ):
    pay_bill( kit )
    page, _ = bank_page()

    class PauseOnce( MemoryPlaybackControl ):
        asked = 0

        def command( self, run_id ):
            self.asked += 1
            return "pause" if self.asked in ( 2, 3 ) else "run"
    _, state, _ = run( kit, page, PreApprovedGate({ 4 }), ConsoleSecretSource( ScriptedConsole( secrets=[ "9" ])), control=PauseOnce())
    assert state == "completed"


def test_invalid_recording_runs_nothing( kit ):
    kit.repository().create( "Pay Bill", [ RecordedAction( action_type="click" )])
    page, _ = bank_page()
    _, state, message = run( kit, page, DenyApprovalGate(), NoSecretSource())
    assert state == "failed" and "nothing was run" in message and page.find_calls == 0


# -- self-healing ------------------------------------------------------------

def test_broken_locator_gets_a_verified_repair_applied_only_with_approval( kit ):
    pay_bill( kit )
    page, els = bank_page()
    pay = els[ "pay" ]                                  # the site renamed the button's id and text
    pay.selectors, pay.text = { "#submit-payment" }, "Pay now securely"
    executor = kit.playback_executor( FixedBrowserLease( page ), PreApprovedGate({ 4 }),
                                      ConsoleSecretSource( ScriptedConsole( secrets=[ "1" ])), clock=FakeClock())
    run_id, state, _ = executor.execute( kit.repository().by_name( "Pay Bill" ), "test", 4 )
    assert state == "failed" and pay.clicks == 0        # never clicked with a guessed locator
    evidence = kit.run_log().run( run_id ).results[ -1 ].diagnostics
    assert evidence.repair_id and [ a.outcome for a in evidence.attempts ] == [ "none", "none" ]
    assert kit.repair_store().get( evidence.repair_id ).verified

    applier = kit.repair_applier()
    ok, why = applier.apply( evidence.repair_id, approved=False )
    assert not ok and "approval" in why
    assert kit.repository().by_name( "Pay Bill" ).steps[ 3 ].locators[ 0 ].value != "#submit-payment"
    ok, why = applier.apply( evidence.repair_id, approved=True )
    assert ok, why
    step = kit.repository().by_name( "Pay Bill" ).steps[ 3 ]
    assert step.locators[ 0 ].value == "#submit-payment" and len( step.locators ) == 3

    _, state, _ = executor.execute( kit.repository().by_name( "Pay Bill" ), "test", 4 )
    assert state == "completed" and pay.clicks == 1


def test_two_lookalikes_get_no_proposal( kit ):
    pay_bill( kit )
    page, els = bank_page()
    els[ "pay" ].selectors = { "#p1" }
    page.add( FakeElement( "button", ( "#p2", ), "Pay now" ))
    executor = kit.playback_executor( FixedBrowserLease( page ), PreApprovedGate({ 4 }), NoSecretSource(), clock=FakeClock())
    run_id, state, _ = executor.execute( kit.repository().by_name( "Pay Bill" ), "test", 4 )
    evidence = kit.run_log().run( run_id ).results[ -1 ].diagnostics
    assert state == "failed" and evidence.repair_id is None and "by hand" in evidence.repair_note


# -- editing and matching ----------------------------------------------------

def test_editor( kit ):
    recording = pay_bill( kit )
    editor    = RepositoryRecordingEditor( kit.repository())
    ids       = [ s.id for s in recording.steps ]
    moved     = editor.move_step( recording.id, 2, 3 )
    assert [ s.id for s in moved.steps ] == [ ids[ 0 ], ids[ 2 ], ids[ 1 ], ids[ 3 ]]
    with pytest.raises( BrowserWorkflowError, match="secret" ):
        editor.set_value( recording.id, 2, "1234" )
    assert editor.set_value( recording.id, 3, "Utilities" ).steps[ 2 ].value == "Utilities"
    inserted = editor.insert_step( recording.id, 4, RecordedAction( action_type="wait",
                                   wait_after=[ WaitCondition( kind="text_present", value="Review" )]))
    assert [ s.action_type for s in inserted.steps ] == [ "navigate", "enter_text", "enter_text", "wait", "click" ]
    assert [ s.position for s in editor.remove_step( recording.id, 4 ).steps ] == [ 1, 2, 3, 4 ]
    copy = editor.duplicate( recording.id, "Pay Bill (copy)" )
    assert len( copy.steps ) == 4 and copy.steps[ 1 ].value is None
    assert editor.delete( copy.id, confirm=False ) is False and kit.repository().by_id( copy.id )
    assert editor.delete( copy.id, confirm=True ) and kit.repository().by_id( copy.id ) is None
    assert editor.describe( recording.id )[ 1 ][ "value" ].startswith( "(secret" )


def test_matcher():
    saved = [ RecordingSummary( id=1, name="Pay Bill", step_count=3 ), RecordingSummary( id=2, name="Fifth Third Login", step_count=2 ),
              RecordingSummary( id=3, name="Pay Bill Twice", step_count=6 )]
    m = WordPlaybackMatcher()
    assert m.match( "pay bill", saved )[ 0 ].id == 1
    assert m.match( "please run the fifth third login playback", saved )[ 0 ].id == 2
    assert m.match( "pay bill twice now", saved )[ 0 ].id == 3
    found, why = m.match( "open the mail", saved )
    assert found is None and "Pay Bill" in why
