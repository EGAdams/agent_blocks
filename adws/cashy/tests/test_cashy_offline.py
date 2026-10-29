"""Offline: Cashy's pieces, with no browser and no network."""
from __future__ import annotations

from pathlib import Path

import pytest

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.models import Period


def _kit( tmp_path: Path ):
    from cashy.kit import CashyKit
    return CashyKit( port=9, db_path=tmp_path / "db.sqlite", data_dir=tmp_path / "cashy",
                     transfers_file=tmp_path / "none.txt", observers=[])


def _option( text: str, kind: str = "button", href: str = "", number: int = 1 ):
    from cashy.models import PageOption
    return PageOption( number=number, kind=kind, tag="a" if kind == "link" else "button", text=text,
                       css=f"#o{ number }", href=href )


# -- reading what the person said ----------------------------------------------

@pytest.mark.parametrize( "said, kinds", [
    ( "Cashy, open Chase",                                [ "open_bank" ]),
    ( "I'm logged in. Open my statements.",               [ "logged_in", "go_to" ]),
    ( "Download my September 2025 statement.",            [ "get_statement" ]),
    ( "download it again",                                [ "get_statement" ]),
    ( "Remember how you did that.",                       [ "remember" ]),
    ( "click the second matching result",                 [ "click_number" ]),
    ( "What options do you see?",                         [ "list_options" ]),
    ( "go back",                                          [ "go_back" ]),
    ( "open the Transfer Activity for the Housing Gas Bill", [ "find_transaction" ]),
])
def test_interpreter_reads_the_mission_phrases( tmp_path: Path, said: str, kinds: list[ str ]) -> None:
    kit = _kit( tmp_path )
    from cashy.conversation.interpreter import RuleChainInterpreter
    from cashy.conversation.rules import default_rules
    intents = RuleChainInterpreter( default_rules( kit.banks(), kit.workflows.period_parser())).interpret( said )
    assert [ i.kind for i in intents ] == kinds


def test_statement_request_carries_bank_and_period( tmp_path: Path ) -> None:
    kit = _kit( tmp_path )
    from cashy.conversation.interpreter import RuleChainInterpreter
    from cashy.conversation.rules import default_rules
    [ intent ] = RuleChainInterpreter( default_rules( kit.banks(), kit.workflows.period_parser())).interpret(
        "get my September 2025 Chase statement" )
    assert intent.bank == "chase" and intent.period == Period( year=2025, month=9 )


# -- what is never kept --------------------------------------------------------

def test_sanitizer_masks_accounts_money_and_query_strings_but_not_hosts() -> None:
    from cashy.storage.sanitizer import mask_text, mask_url
    assert mask_text( "Checking 1234567890 paid $1,204.33" ) == "Checking •7890 paid $…"
    assert mask_text( "September 2025" ) == "September 2025"
    assert mask_url( "http://127.0.0.1:45633/acct/123456789?token=abc#x" ) == "http://127.0.0.1:45633/acct/•6789"


def test_saved_session_holds_no_query_strings_or_account_numbers( tmp_path: Path ) -> None:
    from cashy.models import CashySession
    store = _kit( tmp_path ).sessions()
    store.save( CashySession( id="s", url="https://bank.example/acct/123456789?session=SECRET",
                              options=[ _option( "Account 9876543210" )]))
    raw = ( tmp_path / "db.sqlite" ).read_bytes()
    assert b"SECRET" not in raw and b"123456789" not in raw and b"9876543210" not in raw
    assert store.load( "s" ).url == "https://bank.example/acct/•6789"


# -- states ----------------------------------------------------------------------

def test_a_paused_session_resumes_only_where_it_was() -> None:
    from cashy.models import CashySession
    from cashy.session.machine import TableStateMachine
    from cashy.session.states import ALL_STATES
    machine, session = TableStateMachine( ALL_STATES ), CashySession( state="ready" )
    machine.move( session, "paused" )
    assert session.paused_from == "ready"
    with pytest.raises( BrowserWorkflowError ):
        machine.move( session, "awaiting_user_login" )
    machine.move( session, "ready" )
    assert session.state == "ready" and session.paused_from is None


def test_waiting_for_login_refuses_to_navigate( tmp_path: Path ) -> None:
    from cashy.models import CashySession
    agent = _kit( tmp_path ).agent()
    store = _kit( tmp_path ).sessions()
    store.save( CashySession( id="w", state="awaiting_user_login", bank="chase" ))
    reply = agent.turn( "click Statements", session_id="w" )
    assert reply.state == "awaiting_user_login" and "sign in" in reply.text.lower()


def test_asks_for_the_month_before_anything_else( tmp_path: Path ) -> None:
    from cashy.models import CashySession
    _kit( tmp_path ).sessions().save( CashySession( id="m", state="ready", bank="chase" ))
    reply = _kit( tmp_path ).agent().turn( "download my statement", session_id="m" )
    assert reply.state == "awaiting_user_input" and "Which month and year" in reply.text


# -- risk ------------------------------------------------------------------------

@pytest.mark.parametrize( "text, kind, href, asks", [
    ( "Transfer activity",   "link",   "/pay/activity", False ),
    ( "Pay & transfer",      "link",   "/pay",          False ),
    ( "Pay & transfer",      "link",   "javascript:go()", True ),
    ( "Pay & transfer",      "button", "",              True ),
    ( "Make a payment",      "button", "",              True ),
    ( "Send money",          "link",   "/send",         True ),
    ( "Confirm transfer",    "button", "",              True ),
    ( "Delete statement history", "button", "",         True ),
    ( "Statements",          "link",   "/documents",    False ),
])
def test_risk_asks_about_actions_not_destinations( tmp_path: Path, text: str, kind: str, href: str, asks: bool ) -> None:
    assert _kit( tmp_path ).risk().assess( _option( text, kind, href )).consequential is asks


# -- ranking, records, learning -----------------------------------------------

def test_ranker_prefers_the_exact_caption() -> None:
    from cashy.page.ranker import TokenOptionRanker
    ranked = TokenOptionRanker().rank([ _option( "Statement preferences", number=1 ),
                                        _option( "Statements", number=2 )], [ "statements" ])
    assert ranked[ 0 ].option.text == "Statements"


def test_transfer_hints_suggest_without_assuming( tmp_path: Path ) -> None:
    from cashy.finance.transfers import TextTransferRecords
    records = tmp_path / "t.txt"
    records.write_text( "Transfer: 01\nDate: 2025-09-28\nTo: gas at church\nFrom: River of Life 1, ending in 6285\n"
                        "Amount: $60.57\n\nTransfer: 02\nDate: 2025-09-01\nTo: water dept\nFrom: x, ending in 1111\n"
                        "Amount: $10.00\n" )
    [ hint ] = TextTransferRecords( records ).candidates( "Housing Gas Bill" )
    assert hint.payee == "gas at church" and hint.accounts == [ "•6285" ] and hint.score < 1.0


def test_learner_finds_the_month_by_words_only() -> None:
    from cashy.learning.learner import TrailWorkflowLearner
    from cashy.models import CashySession, CashyTask, NavigationStep
    from cashy.navigation.statements import CalendarStatementLocator
    from browser_workflows.templating.period import CalendarPeriodVocabulary
    session = CashySession( trail_start="https://b.example/home",
                            task=CashyTask( goal="statement_download", period=Period( year=2025, month=9 )),
                            trail=[ NavigationStep( action="click", text="Statements", css="#s", url_before="https://b.example/home",
                                                    url_after="https://b.example/docs" ),
                                    NavigationStep( action="click", text="Oops", css="#x", undone=True ),
                                    NavigationStep( action="click", text="September 2025", css="li:nth-child(2) a",
                                                    url_before="https://b.example/docs" )])
    steps = TrailWorkflowLearner( CalendarStatementLocator( CalendarPeriodVocabulary())).steps( session )
    assert [ s.action_type for s in steps ] == [ "navigate", "click", "click" ]
    assert [ c.strategy for c in steps[ 1 ].locators ] == [ "css", "text" ]
    assert [ c.strategy for c in steps[ 2 ].locators ] == [ "text" ]
    assert steps[ 1 ].wait_after[ 0 ].value == "/docs"


def test_a_new_version_never_replaces_the_old_one( tmp_path: Path ) -> None:
    from cashy.models import WorkflowVersion
    registry = _kit( tmp_path ).registry()
    one = registry.add( WorkflowVersion( bank="chase", goal="statements", version=1, playback="p1" ))
    registry.set_status( one.id, "verified", 7 )
    assert registry.next_version( "chase", "statements" ) == 2
    registry.add( WorkflowVersion( bank="chase", goal="statements", version=2, playback="p2" ))
    assert registry.latest( "chase", "statements" ).playback == "p1"            # v2 unproven
    assert [ v.version for v in registry.versions( "chase", "statements" )] == [ 2, 1 ] or \
           [ v.version for v in registry.versions( "chase", "statements" )] == [ 1, 2 ]


# -- the ADW catalog ------------------------------------------------------------

def test_operations_share_one_agent_and_refuse_bad_input( tmp_path: Path ) -> None:
    from cashy.kit import CashyKit
    from cashy.operations import cashy_operations
    ops = cashy_operations( None, None, factory=lambda port: CashyKit(
        port=port, db_path=tmp_path / "db.sqlite", data_dir=tmp_path / "c", transfers_file=tmp_path / "n", observers=[]))
    assert {"cashy_turn", "cashy_get_statement", "cashy_click", "cashy_list_workflows"} <= set( ops )
    assert not ops[ "cashy_get_statement" ].run({ "period": "someday" }).ok
    assert not ops[ "cashy_open_bank" ].run({ "bank": "Bank of Nowhere" }).ok
    assert "idle" in ops[ "cashy_status" ].run({}).line
    assert ops[ "cashy_list_workflows" ].run({}).line == "No learned banking workflows yet."
