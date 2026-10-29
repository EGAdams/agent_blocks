"""Live: the mission's acceptance conversation, against the pretend bank in
a real (headless) debug Chrome. Set CDP_TEST_PORT to run it."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from .fake_bank import STATEMENT, bank_site  # noqa: F401  (a fixture)

PORT = os.environ.get( "CDP_TEST_PORT" )
pytestmark = pytest.mark.skipif( not PORT, reason="set CDP_TEST_PORT to a local debug Chrome" )

TRANSFERS = """Transfer: 01
Date: 2025-09-28
To: gas at church
From: River of Life 1, ending in 6285
Amount: $60.57
Status: Completed

Transfer: 02
Date: 2025-08-28
To: gas at church
From: River of Life 1, ending in 6285
Amount: $54.10
Status: Completed
"""


def _kit( site: str, tmp_path: Path ):
    from cashy.kit import CashyKit
    from cashy.models import BankProfile

    test_bank = BankProfile( key="chase", title="Chase", names=[ "chase" ], login_url=site + "/login",
                             hosts=[ "127.0.0.1" ])
    transfers = tmp_path / "transfers.txt"
    transfers.write_text( TRANSFERS )
    return CashyKit( port=int( PORT ), db_path=tmp_path / "db.sqlite", data_dir=tmp_path / "cashy",
                     transfers_file=transfers, observers=[], banks=[ test_bank ])


def _sign_in_by_hand( kit, site: str ) -> None:
    """What the person does: sign in, in the tab Cashy opened."""
    browser = kit.connection.attach( kit.connection.preference()[ 0 ], [ "127.0.0.1" ])
    try:
        browser.navigate( site + "/signin" )
    finally:
        browser.release()


def _say( agent, text: str, session: str = "s1" ):
    reply = agent.turn( text, session_id=session )
    print( f"\nyou> { text }\ncashy [{ reply.state }]> { reply.text }" )
    return reply


def _wait_file( folder: Path, name: str ) -> Path:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ( folder / name ).exists():
        time.sleep( 0.2 )
    return folder / name


def test_acceptance_conversation( bank_site: str, tmp_path: Path, monkeypatch ) -> None:
    downloads = tmp_path / "downloads"
    monkeypatch.setenv( "BROWSER_WORKFLOW_DOWNLOAD_DIR", str( downloads ))
    kit   = _kit( bank_site, tmp_path )
    agent = kit.agent()

    reply = _say( agent, "Cashy, open Chase" )
    assert reply.state == "awaiting_user_login", reply.text
    assert "sign in yourself" in reply.text.lower()

    reply = _say( agent, "open my statements" )             # not signed in yet: she must wait
    assert reply.state == "awaiting_user_login", reply.text

    _sign_in_by_hand( kit, bank_site )
    reply = _say( agent, "I'm logged in. Open my statements." )
    assert reply.ok and reply.state == "ready", reply.text
    assert "statements" in reply.text.lower()

    reply = _say( agent, "Download my September 2025 statement." )
    assert reply.ok, reply.text
    assert _wait_file( downloads, "statement_2025-09.pdf" ).read_bytes().startswith( STATEMENT )
    assert "haven't imported it into the books" in reply.text     # never imported

    reply = _say( agent, "Download my September 2025 statement." )         # a duplicate: refuse
    assert "already" in reply.text.lower(), reply.text

    reply = _say( agent, "Remember how you did that." )
    assert reply.ok, reply.text
    versions = kit.registry().versions( "chase", "statement_download" )
    assert len( versions ) == 1 and versions[ 0 ].needs, reply.text

    # -- a later session: the learned workflow, not exploration ----------------
    later = kit.agent()
    _say( later, "Cashy, open Chase", session="s2" )
    _sign_in_by_hand( kit, bank_site )
    _say( later, "I'm logged in", session="s2" )
    reply = _say( later, "get my October 2025 Chase statement", session="s2" )
    assert reply.ok, reply.text
    assert _wait_file( downloads, "statement_2025-10.pdf" ).exists(), reply.text
    assert kit.registry().versions( "chase", "statement_download" )[ 0 ].status == "verified"

    # -- transfer activity, checked against the records ------------------------
    reply = _say( later, "open the Transfer Activity for the Housing Gas Bill", session="s2" )
    assert "is it one of these" in reply.text.lower(), reply.text          # suggests, never assumes
    reply = _say( later, "yes", session="s2" )
    assert "which bank" in reply.text.lower(), reply.text                   # and checks the account
    reply = _say( later, "Chase", session="s2" )
    assert reply.ok, reply.text
    assert "gas at church" in reply.text.lower() and "6285" in reply.text, reply.text
    assert "won't start a transfer" in reply.text, reply.text


def test_never_presses_a_payment_button_on_its_own( bank_site: str, tmp_path: Path ) -> None:
    kit   = _kit( bank_site, tmp_path )
    agent = kit.agent()
    _say( agent, "open Chase" )
    _sign_in_by_hand( kit, bank_site )
    _say( agent, "I'm logged in" )
    reply = _say( agent, "click Make a payment" )
    assert reply.state == "awaiting_user_input", reply.text                # asks first
    reply = _say( agent, "no" )
    browser = kit.connection.attach( kit.connection.preference()[ 0 ], [ "127.0.0.1" ])
    try:
        assert browser.current_url.endswith( "/dashboard" )
    finally:
        browser.release()


def test_another_agent_cannot_approve_a_payment( bank_site: str, tmp_path: Path ) -> None:
    kit   = _kit( bank_site, tmp_path )
    agent = kit.agent()
    _say( agent, "open Chase" )
    _sign_in_by_hand( kit, bank_site )
    _say( agent, "I'm logged in" )
    reply = agent.turn( "click Make a payment", actor="toyota", session_id="s1" )
    assert not reply.ok and "Only the person at the keyboard" in reply.text, reply.text
