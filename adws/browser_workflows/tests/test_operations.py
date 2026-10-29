"""The fine-grained operations, run the way an ADW runs them: by name from
the catalog, with flag-shaped arguments, against a scripted page.

Pinned: an agent's run blocks at the first secret or consequential step
instead of guessing; a person at the terminal can approve and type the
secret; an ad hoc click on "Pay" needs --yes; a password field is never
typed into; edits and history work by playback name; and the composed
execute_saved_playback is load -> validate -> execute.
"""
from __future__ import annotations

import socket

import pytest

from browser_workflows.kit import WorkflowKit
from browser_workflows.operations.catalog import operations
from browser_workflows.playback.lease import FixedBrowserLease

from .fakes import FakeBrowser, FakeClock, FakeElement, ScriptedConsole
from .test_playback import bank_page, pay_bill


class _Attachment:
    def __init__( self, page: FakeBrowser ) -> None:
        self.page, self.opened = page, 0

    def open( self, endpoint ):
        self.opened += 1
        return FixedBrowserLease( self.page )


class PageKit( WorkflowKit ):
    """The real kit, with the debug Chrome swapped for a scripted page."""

    def __init__( self, tmp_path, page: FakeBrowser ) -> None:
        super().__init__( db_path=tmp_path / "db.sqlite", log_path=tmp_path / "logs" / "recorder.jsonl" )
        self.fake = _Attachment( page )
        self._clock = FakeClock()

    def attachment( self ):
        return self.fake

    def clock( self ):
        return self._clock

    def download_directory( self ):
        return str( self._db_path.parent / "downloads" )


@pytest.fixture
def bank( tmp_path ):
    page, els = bank_page()
    kit = PageKit( tmp_path, page )
    pay_bill( kit )
    return kit, page, els


def catalog( kit, console=None ):
    return operations( kit, console or ScriptedConsole())


def test_catalog_names_every_operation_once( bank ):
    kit, _, _ = bank
    names = set( catalog( kit ))
    assert { "start_recording", "stop_recording", "save_recording", "capture_click", "capture_text", "normalize_event",
             "locate_element", "execute_click", "execute_text", "wait_for", "navigate", "execute_step",
             "execute_playback", "execute_saved_playback", "control_playback", "playback_history",
             "repair_locator", "list_recordings", "load_recording", "validate_recording", "edit_playback",
             "agent_request" } <= names
    assert all( op.description for op in catalog( kit ).values())


def test_agent_request_blocks_at_the_secret_and_guesses_nothing( bank ):
    kit, page, els = bank
    out = catalog( kit )[ "agent_request" ].run({ "request": "please run pay bill", "actor": "toyota" })
    assert not out.ok and "blocked" in out.line and "step 3" in out.line
    assert els[ "memo" ].typed == [ "Rent" ] and els[ "pin" ].typed == [] and els[ "pay" ].clicks == 0
    assert kit.run_log().run( out.data[ "run_id" ]).actor == "toyota"


def test_agent_request_refuses_what_it_cannot_match( bank ):
    kit, _, _ = bank
    out = catalog( kit )[ "agent_request" ].run({ "request": "water the plants" })
    assert not out.ok and kit.fake.opened == 0
    assert "Pay Bill" in catalog( kit )[ "agent_request" ].run({ "request": "list playbacks" }).line


@pytest.mark.parametrize( "question", [
    "list playbacks",
    "browser recorder: list playbacks",
    "ask the browser recorder which playbacks there are",
    "have the browser recorder show saved playbacks",
])
def test_routed_list_questions_never_open_the_browser( bank, question ):
    kit, _, _ = bank
    out = catalog( kit )[ "agent_request" ].run({ "request": question })
    assert out.ok and out.line == "saved playbacks: Pay Bill"
    assert kit.fake.opened == 0


def test_a_person_at_the_terminal_can_approve_and_type_the_secret( bank ):
    kit, page, els = bank
    console = ScriptedConsole( answers=[ "YES" ], secrets=[ "4321" ])
    out = catalog( kit, console )[ "execute_playback" ].run({ "name": "Pay Bill", "interactive": True })
    assert out.ok, out.line
    assert els[ "pin" ].typed == [ "4321" ] and els[ "pay" ].clicks == 1
    assert b"4321" not in ( kit._db_path ).read_bytes()


def test_composed_execute_saved_playback_loads_validates_then_runs( bank ):
    kit, _, els = bank
    seq = catalog( kit )[ "execute_saved_playback" ]
    out = seq.run({ "name": "Pay Bill", "approve": "4" })
    assert [ name for name, _ in seq.trail ] == [ "load_recording", "validate_recording", "execute_playback" ]
    assert not out.ok and "step 3 blocked" in out.line        # --approve covers the click, not the PIN


def test_download_folder_is_configured_before_any_playback_navigation( bank ):
    kit, page, _ = bank
    navigate = page.navigate

    def navigate_after_setup( url ):
        assert page.download_directory == kit.download_directory()
        navigate( url )

    page.navigate = navigate_after_setup
    out = catalog( kit )[ "execute_playback" ].run({ "name": "Pay Bill" })
    assert "download folder:" in out.line
    assert out.data[ "download_directory" ] == kit.download_directory()


def test_failed_download_setup_releases_the_browser_and_runs_nothing( bank ):
    from browser_workflows.errors import BrowserWorkflowError

    kit, page, _ = bank
    released = []
    lease = FixedBrowserLease( page )
    lease.release = lambda: released.append( True )
    kit.fake.open = lambda endpoint: lease

    def broken( directory ):
        raise BrowserWorkflowError( "cannot configure downloads" )

    page.configure_downloads = broken
    out = catalog( kit )[ "execute_playback" ].run({ "name": "Pay Bill" })
    assert not out.ok and "cannot configure downloads" in out.line
    assert released == [ True ] and page.visits == [] and kit.run_log().recent() == []


def test_ad_hoc_click_on_pay_needs_yes( bank ):
    kit, _, els = bank
    ops = catalog( kit )
    out = ops[ "execute_click" ].run({ "text": "Pay now" })
    assert not out.ok and "blocked" in out.line and els[ "pay" ].clicks == 0
    out = ops[ "execute_click" ].run({ "text": "Pay now", "yes": True })
    assert out.ok and els[ "pay" ].clicks == 1


def test_execute_text_never_types_into_a_password_field( bank ):
    kit, _, els = bank
    ops = catalog( kit )
    assert not ops[ "execute_text" ].run({ "css": "#pin", "value": "1111" }).ok and els[ "pin" ].typed == []
    assert ops[ "execute_text" ].run({ "css": "#memo", "value": "Gas" }).ok and els[ "memo" ].typed == [ "Gas" ]
    assert getattr( ops[ "execute_text" ], "redact" ) == ( "value", )


def test_locate_navigate_and_wait( bank ):
    kit, page, _ = bank
    ops = catalog( kit )
    assert ops[ "locate_element" ].run({ "text": "Pay now" }).ok
    assert not ops[ "locate_element" ].run({ "css": "#nope" }).ok
    assert not ops[ "navigate" ].run({ "url": "file:///etc/passwd" }).ok
    assert ops[ "navigate" ].run({ "url": "https://bank.test/home" }).ok and page.visits == [ "https://bank.test/home" ]
    assert ops[ "wait_for" ].run({ "kind": "url_contains", "value": "/home" }).ok
    assert not ops[ "wait_for" ].run({ "kind": "url_contains", "value": "/never", "timeout": 1 }).ok


def test_execute_step_runs_one_saved_step( bank ):
    kit, _, els = bank
    out = catalog( kit )[ "execute_step" ].run({ "name": "Pay Bill", "position": 2 })
    assert out.ok and els[ "memo" ].typed == [ "Rent" ]
    assert not catalog( kit )[ "execute_step" ].run({ "name": "Pay Bill", "position": 9 }).ok


def test_editing_listing_validating_and_history_by_name( bank ):
    kit, _, _ = bank
    ops = catalog( kit )
    assert "Pay Bill (4 steps)" in ops[ "list_recordings" ].run({}).line
    assert ops[ "validate_recording" ].run({ "name": "Pay Bill" }).ok
    assert "(secret" in ops[ "load_recording" ].run({ "name": "Pay Bill" }).line
    assert not ops[ "edit_playback" ].run({ "name": "Pay Bill", "edit": "value", "position": 3, "value": "x" }).ok
    assert ops[ "edit_playback" ].run({ "name": "Pay Bill", "edit": "duplicate", "new_name": "Pay Rent" }).ok
    assert not ops[ "edit_playback" ].run({ "name": "Pay Rent", "edit": "delete" }).ok
    assert ops[ "edit_playback" ].run({ "name": "Pay Rent", "edit": "delete", "yes": True }).ok
    assert ops[ "edit_playback" ].run({ "name": "Pay Bill", "edit": "rename", "new_name": "Pay The Bill" }).ok

    run = ops[ "execute_playback" ].run({ "name": "Pay The Bill" })
    history = ops[ "playback_history" ].run({ "name": "Pay The Bill" })
    assert history.ok and "blocked" in history.line
    detail = ops[ "playback_history" ].run({ "run_id": run.data[ "run_id" ]})
    assert "enter_text" in detail.line and "blocked" in detail.line
    assert not ops[ "control_playback" ].run({ "run_id": run.data[ "run_id" ], "command": "stop" }).ok


def test_no_debug_chrome_is_one_line_naming_the_adw_that_starts_it( tmp_path ):
    with socket.socket() as s:
        s.bind(( "127.0.0.1", 0 ))
        port = s.getsockname()[ 1 ]
    kit = WorkflowKit( db_path=tmp_path / "db.sqlite", log_path=tmp_path / "r.jsonl" )
    out = operations( kit, ScriptedConsole())[ "locate_element" ].run({ "css": "#x", "port": port })
    assert not out.ok and "adw_interface_browser_debug_open.py" in out.line
