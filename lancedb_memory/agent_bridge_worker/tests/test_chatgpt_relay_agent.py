"""ChatGptRelayAgent and its uv command adapter, offline. Nothing is typed into anybody's ChatGPT.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_chatgpt_relay_agent
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.chatgpt_relay_process import UvChatGptCommands
from agent_bridge_worker.chatgpt_relay_agent import READS, WRITES, ChatGptRelayAgent, RelayCommandFailed
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.policy import ScopedPolicy
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_comms.tests.fakes import FakeClock

CAPS  = { "chatgpt-relay": { "inspect": False, "implement": True }}
MODES = frozenset({ "read_only", "workspace_write" })


def task( prompt: dict | str, operation: str = "implement", **over ) -> ClaimedTask:
    mode   = "read_only" if operation == "inspect" else "workspace_write"
    fields = dict( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1,
                   agent="chatgpt-relay", workspace="chatgpt-relay", operation=operation, approval_mode=mode,
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
            raise RelayCommandFailed( self.error )
        return self.answer

    def stop( self ):
        self.stopped = True


class FakeCommands:
    def __init__( self, answer=None, **kw ):
        self.answer, self.kw, self.calls, self.last = answer, kw, [], None

    def start( self, operation, flags, switches ):
        self.calls.append(( operation, flags, switches ))
        self.last = FakeRunning( self.answer, **self.kw )
        return self.last


def answered( line="bati says: RELAY OK", **data ) -> dict:
    return { "ok": True, "line": line, "adw_id": "5ea1a596", "data": { "settled": True, **data }}


def run( commands, t, signals=None ):
    seq = iter( signals or [] )
    return ChatGptRelayAgent( commands, FakeClock(), poll_seconds=0.1 ).run( t, Path( "." ), lambda: next( seq, "continue" ))


def test_ask_runs_the_relay_adw_and_reports_the_reply_with_its_execution_id():
    commands = FakeCommands( answered( reply="RELAY OK" ))
    out = run( commands, task({ "relay": "ask", "instance": "bati", "message": "are you there?" }))
    assert commands.calls == [( "ask", { "instance": "bati", "message": "are you there?" }, frozenset())]
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "adw_id" ] == "5ea1a596"
    assert out.details[ "verified" ] is True and out.body.endswith( "RELAY OK" )


def test_an_ask_that_never_settled_is_not_reported_as_the_answer():
    """The deadline won while the reply was still being written. A half-written sentence is not a decision."""
    half = { "ok": True, "line": "bati is still writing.", "adw_id": "beef0001",
             "data": { "settled": False, "reply": "The plat was fil" }}
    out = run( FakeCommands( half ), task({ "relay": "ask", "message": "what did we decide?" }))
    assert out.kind == OutcomeKind.FAILED and out.retryable is False and out.details[ "verified" ] is False


def test_a_refusal_from_the_relay_is_a_failure_with_its_words():
    refusal = { "ok": False, "adw_id": "beef0002", "line": "I did not send that: it looks like a credential.",
                "data": { "cause": "credential" }}
    out = run( FakeCommands( refusal ), task({ "relay": "ask", "message": "my password is hunter2" }))
    assert out.kind == OutcomeKind.FAILED and not out.retryable and out.details[ "cause" ] == "credential"
    assert "hunter2" not in out.summary and "hunter2" not in out.body


def test_reads_run_as_inspect_and_changes_as_implement():
    assert run( FakeCommands(), task({ "relay": "read" }, "implement" )).kind == OutcomeKind.REFUSED
    assert run( FakeCommands(), task({ "relay": "ask", "message": "hi" }, "inspect" )).kind == OutcomeKind.REFUSED
    assert run( FakeCommands( answered()), task({ "relay": "read" }, "inspect" )).kind == OutcomeKind.SUCCEEDED


def test_there_is_no_way_to_quit_the_browser_from_here():
    """That Chrome is signed in to EG's account. Closing it costs the login, so no table names it."""
    assert "quit" not in READS | WRITES and "close" not in READS | WRITES
    commands = FakeCommands( answered())
    for prompt in ({ "relay": "quit" }, { "relay": "relay.quit" }, { "relay": "close" }, { "relay": "shutdown" }):
        assert run( commands, task( prompt )).kind == OutcomeKind.REFUSED, prompt
    assert commands.calls == []


def test_a_new_chat_needs_an_explicit_yes_because_it_abandons_the_thread_on_screen():
    commands = FakeCommands( answered( "a new chat is open on bati." ))
    assert run( commands, task({ "relay": "new_chat" })).kind == OutcomeKind.REFUSED
    assert run( commands, task({ "relay": "new_chat", "confirm": "yes" })).kind == OutcomeKind.REFUSED
    assert run( commands, task({ "relay": "new_chat", "confirm": True })).kind == OutcomeKind.SUCCEEDED
    assert commands.calls == [( "new_chat", {}, frozenset({ "confirm" }))]


def test_free_text_unknown_fields_and_missing_messages_are_refused_before_anything_is_typed():
    commands = FakeCommands( answered())
    for prompt in ( "ask my other session about the plat", { "relay": "ask" }, { "relay": "type" },
                    { "relay": "ask", "message": "hi", "cmd": "calc.exe" }, { "relay": "ask", "message": [ "hi" ] },
                    { "relay": "ask", "message": "hi", "instance": "x" * 600 }, { "relay": "ask", "message": "x" * 7500 }):
        assert run( commands, task( prompt )).kind == OutcomeKind.REFUSED, prompt
    assert commands.calls == []


def test_the_public_spelling_relay_dot_ask_is_accepted():
    commands = FakeCommands( answered())
    out = run( commands, task({ "relay": "relay.ask", "message": "hi" }))
    assert out.kind == OutcomeKind.SUCCEEDED and commands.calls[ 0 ][ 0 ] == "ask"


def test_the_two_word_flag_keeps_the_adws_spelling():
    commands = FakeCommands( answered())
    run( commands, task({ "relay": "ask", "message": "hi", "stable_seconds": 4, "hops": 2, "origin": "bati" }))
    assert commands.calls[ 0 ][ 1 ] == { "message": "hi", "stable-seconds": "4", "hops": "2", "origin": "bati" }


def test_a_crashed_relay_adw_is_an_honest_failure_not_a_retry():
    out = run( FakeCommands( error="exit status 1, no outcome file" ), task({ "relay": "ask", "message": "hi" }))
    assert out.kind == OutcomeKind.FAILED and out.retryable is False and "no outcome" in out.summary


def test_a_read_is_stopped_when_cancelled_but_a_sent_message_runs_to_its_end():
    """Once a message is on its way, the reply is coming whether or not anyone is still waiting for it."""
    # The first signal is read before anything starts, so "continue" then "cancel" is a cancel mid-flight;
    # a bare "cancel" would never reach the running command at all.
    nothing_started = FakeCommands( answered(), ticks=3 )
    assert run( nothing_started, task({ "relay": "read" }, "inspect" ), signals=[ "cancel" ]).kind == OutcomeKind.CANCELLED
    assert nothing_started.calls == []
    reading = FakeCommands( answered(), ticks=3 )
    assert run( reading, task({ "relay": "read" }, "inspect" ), signals=[ "continue", "cancel" ]).kind == OutcomeKind.CANCELLED
    assert reading.last.stopped
    asking = FakeCommands( answered(), ticks=3 )
    out = run( asking, task({ "relay": "ask", "message": "hi" }), signals=[ "continue", "cancel" ])
    assert out.kind == OutcomeKind.SUCCEEDED and not asking.last.stopped


def test_the_policy_lets_the_relay_do_both_modes_for_gateway_senders_only():
    policy = ScopedPolicy( CAPS, frozenset({ "chatgpt-relay" }), MODES )   # agent and workspace are one name
    assert policy.refusal( task({ "relay": "ask", "message": "hi" })) is None
    assert policy.refusal( task({ "relay": "read" }, "inspect" )) is None
    assert policy.refusal( task({ "relay": "ask" }, sender="someone-else" )) is not None
    assert policy.refusal( task({ "relay": "ask" }, agent="notes" )) is not None


def test_the_uv_adapter_builds_a_fixed_argv_and_a_message_cannot_become_an_option():
    commands = UvChatGptCommands( Path( "/uv" ), Path( "/adw.py" ), Path( "." ))
    argv = commands.argv( "ask", { "message": "--help", "instance": "bati" }, frozenset({ "no_wait" }), "/tmp/r.json" )
    assert argv == [ "/uv", "run", "--quiet", "/adw.py", "relay.ask", "--instance=bati", "--message=--help",
                     "--no-wait", "--json-out=/tmp/r.json" ]
    try:
        commands.argv( "quit", {}, frozenset(), "/tmp/r" )
    except RelayCommandFailed:
        return
    raise AssertionError( "an unknown operation must not become a command" )


def test_every_operation_this_worker_offers_exists_in_the_relay_adw():
    """The drift test. These two tables are in different packages and a rename on either side would be
    found by a caller at runtime -- "the relay does not offer ask" or "usage: adw_chatgpt_relay.py" --
    rather than here."""
    sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 3 ] / "adws" ))
    from chatgpt_relay.catalog import PUBLIC
    assert [ name for name in sorted( READS | WRITES ) if f"relay.{ name }" not in PUBLIC ] == []
    assert "relay.quit" not in PUBLIC and "relay.close" not in PUBLIC


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )
