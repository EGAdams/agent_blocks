"""BrowserAgent and its uv command adapter, offline. No browser starts.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_browser_agent
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.browser_process import UvBrowserCommands
from agent_bridge_worker.browser_agent import BrowserAgent, BrowserCommandFailed
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.policy import ScopedPolicy
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_comms.tests.fakes import FakeClock

CAPS  = { "browser": { "inspect": False, "implement": True }}
MODES = frozenset({ "read_only", "workspace_write" })


def task( prompt: dict | str, operation: str = "implement", **over ) -> ClaimedTask:
    mode   = "read_only" if operation == "inspect" else "workspace_write"
    fields = dict( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1, agent="browser",
                   workspace="browser", operation=operation, approval_mode=mode,
                   prompt=prompt if isinstance( prompt, str ) else json.dumps( prompt ))
    fields.update( over )
    return ClaimedTask( **fields )


class FakeRunning:
    def __init__( self, answer, ticks=0, error=None ):
        self.answer, self.ticks, self.error, self.stopped = answer, ticks, error, False

    def poll( self ):
        if self.ticks > 0:
            self.ticks -= 1
            return None
        if self.error:
            raise BrowserCommandFailed( self.error )
        return self.answer

    def stop( self ):
        self.stopped = True


class FakeCommands:
    def __init__( self, answer=None, **kw ):
        self.answer, self.kw, self.calls, self.last = answer, kw, [], None

    def start( self, operation, flags, confirm ):
        self.calls.append(( operation, flags, confirm ))
        self.last = FakeRunning( self.answer, **self.kw )
        return self.last


def verified( line="chrome started on this computer (Chrome/1); 1 tab(s) open.", **data ) -> dict:
    return { "ok": True, "line": line, "adw_id": "abcd1234", "data": { "verified": True, **data }}


def run( commands, t, signals=None ):
    seq = iter( signals or [] )
    return BrowserAgent( commands, FakeClock(), poll_seconds=0.1 ).run( t, Path( "." ), lambda: next( seq, "continue" ))


def test_open_runs_the_adw_and_reports_the_verified_result_with_its_execution_id():
    commands = FakeCommands( verified( started=True ))
    out = run( commands, task({ "browser": "open", "url": "https://example.com", "target": "edge" }))
    assert commands.calls == [( "open", { "url": "https://example.com", "browser": "edge" }, False )]
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "adw_id" ] == "abcd1234" and out.details[ "verified" ] is True


def test_a_queued_or_unverified_answer_is_not_a_success():
    out = run( FakeCommands({ "ok": True, "line": "did something", "data": {}}), task({ "browser": "refresh" }))
    assert out.kind == OutcomeKind.FAILED and out.retryable is False and out.details[ "verified" ] is False


def test_a_refusal_from_the_adw_is_a_failure_with_its_words():
    out = run( FakeCommands({ "ok": False, "line": "I did not click: 'Pay' looks consequential.", "data": { "needs_confirmation": True }}),
               task({ "browser": "click", "text": "Pay now" }))
    assert out.kind == OutcomeKind.FAILED and "consequential" in out.summary and out.details[ "needs_confirmation" ]


def test_confirm_is_passed_only_when_it_is_exactly_true():
    commands = FakeCommands( verified())
    run( commands, task({ "browser": "click", "text": "Pay", "confirm": True }))
    run( commands, task({ "browser": "click", "text": "Pay", "confirm": "yes" }))
    assert [ c[ 2 ] for c in commands.calls ] == [ True, False ]


def test_reads_run_as_inspect_and_return_the_page_text_in_the_body():
    commands = FakeCommands( verified( "Example at https://e.example: 5 characters.", text="Hello" ))
    out = run( commands, task({ "browser": "read" }, "inspect" ))
    assert out.kind == OutcomeKind.SUCCEEDED and out.body.endswith( "Hello" )


def test_the_operation_and_the_mode_must_agree():
    assert run( FakeCommands(), task({ "browser": "read" }, "implement" )).kind == OutcomeKind.REFUSED
    assert run( FakeCommands(), task({ "browser": "open" }, "inspect" )).kind == OutcomeKind.REFUSED


def test_free_text_unknown_fields_and_missing_values_are_refused_before_anything_runs():
    commands = FakeCommands( verified())
    for prompt in ( "open a browser", { "browser": "format_disk" }, { "browser": "open", "cmd": "calc.exe" },
                    { "browser": "navigate" }, { "browser": "close" }, { "browser": "select_tab" }, { "browser": "click" },
                    { "browser": "open", "url": "x" * 600 }, { "browser": "open", "url": [ "a" ] }):
        assert run( commands, task( prompt )).kind == OutcomeKind.REFUSED, prompt
    assert commands.calls == []


def test_the_public_spelling_browser_dot_open_is_accepted():
    commands = FakeCommands( verified())
    assert run( commands, task({ "browser": "browser.open" })).kind == OutcomeKind.SUCCEEDED and commands.calls[ 0 ][ 0 ] == "open"


def test_a_crashed_adw_is_an_honest_failure_not_a_retry():
    out = run( FakeCommands( error="exit status 1, no outcome file" ), task({ "browser": "open" }))
    assert out.kind == OutcomeKind.FAILED and out.retryable is False and "no outcome" in out.summary


def test_a_read_is_stopped_when_cancelled_but_a_change_runs_to_its_end():
    reading = FakeCommands( verified(), ticks=3 )
    assert run( reading, task({ "browser": "tabs" }, "inspect" ), signals=[ "cancel" ]).kind == OutcomeKind.CANCELLED or reading.last.stopped
    changing = FakeCommands( verified(), ticks=3 )
    out = run( changing, task({ "browser": "navigate", "url": "https://a.example" }), signals=[ "continue", "cancel" ])
    assert out.kind == OutcomeKind.SUCCEEDED and not changing.last.stopped


def test_the_policy_lets_the_browser_agent_do_both_modes_for_gateway_senders_only():
    policy = ScopedPolicy( CAPS | { "browser": { "inspect": False, "implement": True }}, frozenset({ "browser" }), MODES )
    assert policy.refusal( task({ "browser": "open" })) is None
    assert policy.refusal( task({ "browser": "read" }, "inspect" )) is None
    assert policy.refusal( task({ "browser": "open" }, sender="someone-else" )) is not None
    assert policy.refusal( task({ "browser": "open" }, workspace="browser", agent="notes" )) is not None


def test_the_uv_adapter_builds_a_fixed_argv_and_values_cannot_become_options():
    commands = UvBrowserCommands( Path( "/uv" ), Path( "/adw.py" ), Path( "." ))
    argv = commands.argv( "click", { "text": "--help", "browser": "edge" }, True, "/tmp/r.json" )
    assert argv == [ "/uv", "run", "--quiet", "/adw.py", "browser.click", "--browser=edge", "--text=--help", "--confirm", "--json-out=/tmp/r.json" ]
    try:
        commands.argv( "rm", {}, False, "/tmp/r" )
    except BrowserCommandFailed:
        return
    raise AssertionError( "an unknown operation must not become a command" )


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )
