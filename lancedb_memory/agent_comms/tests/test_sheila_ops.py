"""SheilaOperations, offline: status, Bridge view, a real Post Office round trip, allowlisted ADWs, refusals.

    cd lancedb_memory
    .venv/bin/python agent_comms/tests/test_sheila_ops.py

No network and no Pi: the Pi executor is a scripted double, the gateway view is a fake, the ADW runner is a fake,
and the message round trip runs through a real post office in a tmpdir (real registry, validator, mailboxes and
reply waiter; the recipient's worker is drained in-process instead of being launched).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.cashy_agent import Machine
from agent_comms import factory
from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.executors.basic import HelloWorldExecutor, ScriptedExecutor
from agent_comms.executors.sheila_ops import (
    AdwEntry, BridgeOperation, CommandOutcome, JsonlSheilaLedger, LedgerEntry, MessageOperation, RunAdwOperation,
    SheilaOperations, StatusOperation, body_of,
)
from agent_comms.models import Envelope, MessageKind, TaskResult
from agent_comms.tests.fakes import FakeClock

MACHINES = Path( __file__ ).resolve().parents[ 2 ] / "agent_bridge_worker" / "machines.json"


def envelope( text: str, task_type: str = "inspect", sender: str = "chatgpt-bridge" ) -> Envelope:
    return Envelope( id="20261126T000000Z_aaaaaaaa_from_chatgpt-bridge", kind=MessageKind.REQUEST, sender=sender,
                     recipient="sheila", task_type=task_type, text=text, correlation_id="c",
                     context={ "bridge_task_id": "t_" + "0" * 24 })


class FakeRunner:
    def __init__( self, outcome: CommandOutcome ) -> None:
        self.outcome, self.calls = outcome, []

    def run( self, argv, cwd, timeout_seconds, env=None ) -> CommandOutcome:
        self.calls.append( list( argv ))
        return self.outcome


class FakeView:
    def __init__( self, agents=None, ok: bool = True ) -> None:
        self._agents, self._ok = agents, ok

    def health( self ) -> dict:
        return { "ok": self._ok, "status": 200 if self._ok else 0 }

    def agents( self ):
        return self._agents


class Probe:
    def __init__( self, name: str, value: dict ) -> None:
        self.name, self._value = name, value

    def look( self ) -> dict:
        return self._value


def ledger() -> JsonlSheilaLedger:
    return JsonlSheilaLedger( Path( tempfile.mkdtemp()) / "ledger.jsonl" )


def wrap( *ops, inner=None, book=None ):
    pi = inner or ScriptedExecutor([ TaskResult( ok=True, text="pi answered", performer="pi:read-only" )], performer="pi:read-only" )
    return SheilaOperations( pi, list( ops ), book or ledger()), pi


def test_free_text_goes_to_pi_and_is_ledgered() -> None:
    book = ledger()
    ops, pi = wrap( book=book )
    result = asyncio.run( ops.execute( envelope( "what is in hello_sheila.py?" )))
    assert result.text == "pi answered" and len( pi.seen ) == 1
    entry = book.entries()[ -1 ]
    assert entry.kind == "pi" and entry.ok and entry.bridge_task_id == "t_" + "0" * 24


def test_json_that_is_not_an_operation_also_goes_to_pi() -> None:
    ops, pi = wrap()
    asyncio.run( ops.execute( envelope( '{"question": "is this json?"}' )))
    assert len( pi.seen ) == 1


def test_status_reports_facts_blockers_and_last_successes() -> None:
    book = ledger()
    book.append( LedgerEntry( at="2026-11-26T00:00:00Z", kind="run_adw", task_type="inspect", ok=True, adw_id="abcd1234", adw="x" ))
    book.append( LedgerEntry( at="2026-11-26T00:01:00Z", kind="pi", task_type="implement", ok=False, summary="model_mismatch" ))
    status = StatusOperation([ Probe( "pi", { "installed": True, "version": "0.74.2" }), Probe( "login", { "ok": True }),
                               Probe( "jail", { "present": True }), Probe( "worker", { "active": False })], book, "BATI", "efef782" )
    ops, pi = wrap( status, book=book )
    result = asyncio.run( ops.execute( envelope( '{"sheila": "status"}' )))
    snap = body_of( result.text )
    assert result.ok and not pi.seen
    assert snap[ "machine" ] == "BATI" and snap[ "version" ] == "efef782" and snap[ "observed_at" ].endswith( "Z" )
    assert snap[ "runtime_state" ] == "degraded" and snap[ "blockers" ] == [ "worker: the machine-lane Bridge worker is not active" ]
    assert snap[ "last_adw_success" ][ "adw_id" ] == "abcd1234"
    assert snap[ "last_error" ][ "summary" ] == "model_mismatch"
    assert snap[ "current_task" ][ "bridge_task_id" ] == "t_" + "0" * 24
    # the Bridge's summary is the first line: a sentence a person can read, never a bare "{"
    assert result.text.splitlines()[ 0 ].startswith( "Sheila on BATI: degraded -- worker: the machine-lane Bridge worker is not active" )


def test_status_says_ready_when_nothing_blocks() -> None:
    status = StatusOperation([ Probe( "pi", { "installed": True, "version": "0.74.2" }), Probe( "login", { "ok": True }),
                               Probe( "jail", { "present": True }), Probe( "worker", { "active": True })], ledger(), "BATI", "v" )
    assert status.snapshot()[ "runtime_state" ] == "ready"


def test_a_broken_probe_is_reported_not_raised() -> None:
    class Broken:
        name = "pi"
        def look( self ):
            raise OSError( "gone" )
    snap = StatusOperation([ Broken()], ledger(), "BATI", "v" ).snapshot()
    assert "gone" in snap[ "facts" ][ "pi" ][ "error" ] and snap[ "runtime_state" ] == "degraded"


def test_run_adw_runs_only_the_list_and_returns_the_adw_id() -> None:
    runner = FakeRunner( CommandOutcome( code=0, stdout="adw_id: 26ac2990\nBATI: 5/5 checks passing\n" ))
    adws   = { "machine_watch_dry_run": AdwEntry( argv=[ "lancedb_memory/.venv/bin/python", "adws/adw_machine_watch.py", "--dry-run" ])}
    ops, _ = wrap( RunAdwOperation( adws, Path( "/ws" ), runner ))
    result = asyncio.run( ops.execute( envelope( '{"sheila": "run_adw", "adw": "machine_watch_dry_run"}' )))
    body   = body_of( result.text )
    assert result.ok and body[ "adw_id" ] == "26ac2990" and body[ "exit_code" ] == 0
    assert runner.calls == [[ "/ws/lancedb_memory/.venv/bin/python", "/ws/adws/adw_machine_watch.py", "--dry-run" ]]
    assert result.details[ "adw_id" ] == "26ac2990"
    assert result.text.splitlines()[ 0 ] == "machine_watch_dry_run: succeeded, adw_id 26ac2990, exit 0 in " + str( body[ "seconds" ]) + "s"
    refused = asyncio.run( ops.execute( envelope( '{"sheila": "run_adw", "adw": "rm_everything"}' )))
    assert not refused.ok and "machine_watch_dry_run" in refused.text and len( runner.calls ) == 1


def test_a_failing_adw_is_a_failed_result_with_its_output() -> None:
    runner = FakeRunner( CommandOutcome( code=1, stdout="adw_id: 0badf00d\n", stderr="check failed" ))
    ops, _ = wrap( RunAdwOperation({ "x": AdwEntry( argv=[ "/bin/false" ])}, Path( "/ws" ), runner ))
    result = asyncio.run( ops.execute( envelope( '{"sheila": "run_adw", "adw": "x"}' )))
    assert not result.ok and body_of( result.text )[ "stderr_tail" ] == "check failed"


def test_a_write_adw_runs_only_as_implement() -> None:
    runner = FakeRunner( CommandOutcome( code=0 ))
    ops, _ = wrap( RunAdwOperation({ "w": AdwEntry( argv=[ "/bin/true" ], writes=True )}, Path( "/ws" ), runner ))
    refused = asyncio.run( ops.execute( envelope( '{"sheila": "run_adw", "adw": "w"}', "inspect" )))
    assert not refused.ok and "implement" in refused.text and not runner.calls
    done = asyncio.run( ops.execute( envelope( '{"sheila": "run_adw", "adw": "w"}', "implement" )))
    assert done.ok and len( runner.calls ) == 1


def test_a_read_operation_is_refused_as_implement() -> None:
    ops, _ = wrap( BridgeOperation( FakeView([])))
    result = asyncio.run( ops.execute( envelope( '{"sheila": "bridge"}', "implement" )))
    assert not result.ok and "inspect" in result.text


def test_unknown_operation_and_unknown_fields_are_refused() -> None:
    ops, pi = wrap( BridgeOperation( FakeView([])))
    unknown = asyncio.run( ops.execute( envelope( '{"sheila": "format_disk"}' )))
    assert not unknown.ok and "bridge" in unknown.text and not pi.seen
    extra = asyncio.run( ops.execute( envelope( '{"sheila": "bridge", "url": "http://evil"}' )))
    assert not extra.ok and "unknown field" in extra.text


def test_bridge_view_discovers_agents_or_says_it_cannot() -> None:
    ops, _ = wrap( BridgeOperation( FakeView([{ "name": "sheila", "operations": [ "inspect" ], "workspaces": [ "sheila" ]}])))
    body = body_of( asyncio.run( ops.execute( envelope( '{"sheila": "bridge"}' ))).text )
    assert body[ "agents" ][ 0 ][ "name" ] == "sheila" and body[ "discovery" ] == "ok"
    assert body[ "this_task" ][ "bridge_task_id" ] == "t_" + "0" * 24
    ops, _ = wrap( BridgeOperation( FakeView( None )))
    body = body_of( asyncio.run( ops.execute( envelope( '{"sheila": "bridge"}' ))).text )
    assert body[ "agents" ] is None and body[ "discovery" ].startswith( "unavailable" )
    down, _ = wrap( BridgeOperation( FakeView([], ok=False )))
    assert not asyncio.run( down.execute( envelope( '{"sheila": "bridge"}' ))).ok


def test_the_http_view_names_its_client_because_the_gateway_refuses_python_urllib() -> None:
    """Regression, 2026-11-26: the live front end answered 406 to "Python-urllib/3.14", so discovery failed on BATI."""
    import http.server
    import threading
    from agent_comms.executors.sheila_ops import HttpBridgeView
    seen: list[ tuple[ str, str ]] = []

    class Gate( http.server.BaseHTTPRequestHandler ):
        def do_GET( self ) -> None:
            agent = self.headers.get( "User-Agent", "" )
            seen.append(( self.path, agent ))
            if agent.startswith( "Python-urllib" ):
                self.send_response( 406 ); self.end_headers(); return
            if self.path.endswith( "/agents" ) and self.headers.get( "Authorization" ) != "Bearer tok":
                self.send_response( 401 ); self.end_headers(); return
            payload = b'{"ok": true}' if self.path.endswith( "/health" ) else b'{"agents": [{"name": "sheila"}]}'
            self.send_response( 200 ); self.send_header( "Content-Type", "application/json" ); self.end_headers()
            self.wfile.write( payload )

        def log_message( self, *args ) -> None:
            pass

    server = http.server.HTTPServer(( "127.0.0.1", 0 ), Gate )
    threading.Thread( target=server.serve_forever, daemon=True ).start()
    folder = Path( tempfile.mkdtemp( prefix="sheila_view_" ))
    try:
        ( folder / "token" ).write_text( "tok\n" )
        view = HttpBridgeView( f"http://127.0.0.1:{ server.server_port }/v1", folder / "token" )
        assert view.health()[ "ok" ] and view.agents() == [{ "name": "sheila" }]
        assert all( agent.startswith( "sheila/" ) for _, agent in seen ) and len( seen ) == 2
    finally:
        server.shutdown(); shutil.rmtree( folder, ignore_errors=True )


class PostOfficeHarness:
    """A real post office in a tmpdir over the real registry; hello-world is drained in-process while Sheila waits."""

    def __init__( self ) -> None:
        self.dir   = Path( tempfile.mkdtemp( prefix="sheila_po_" ))
        self.clock = FakeClock()
        self.kit   = factory.build_post_office( root=self.dir, launcher=RecordingLauncher(), clock=self.clock,
                                                builders={ "hello_world": lambda card: HelloWorldExecutor()},
                                                forward_failures_to_mechanic=False )
        self.clock.on_sleep = lambda: asyncio.run( self.kit.worker_for( "hello-world" ).drain())

    def close( self ) -> None:
        shutil.rmtree( self.dir, ignore_errors=True )


def test_message_is_a_real_correlated_post_office_round_trip() -> None:
    h = PostOfficeHarness()
    try:
        book = ledger()
        op   = MessageOperation( h.kit.dispatcher, h.kit.replies, { "hello-world": [ "hello" ]}, wait_seconds=30 )
        ops, _ = wrap( op, book=book )
        result = asyncio.run( ops.execute( envelope( '{"sheila": "message", "to": "hello-world", "task_type": "hello", "text": "ping"}' )))
        body = body_of( result.text )
        assert result.ok, result.text
        assert body[ "reply_text" ] == "Hello World" and body[ "correlation_id" ] == body[ "sent_task_id" ]
        assert body[ "sent_task_id" ].endswith( "_from_sheila" ) and body[ "outcome" ] == "succeeded"
        sent = h.kit.transport.box( "hello-world", "completed" )
        assert [ e.id for e in sent ] == [ body[ "sent_task_id" ]] and sent[ 0 ].sender == "sheila"
        assert sent[ 0 ].parent_id == envelope( "" ).id              # traced back to the Bridge task's envelope
        assert book.entries()[ -1 ].kind == "message" and book.entries()[ -1 ].ok
    finally:
        h.close()


def test_message_to_anyone_off_the_list_is_refused_before_the_post_office() -> None:
    h = PostOfficeHarness()
    try:
        ops, _ = wrap( MessageOperation( h.kit.dispatcher, h.kit.replies, { "hello-world": [ "hello" ]}))
        result = asyncio.run( ops.execute( envelope( '{"sheila": "message", "to": "yolanda", "task_type": "message", "text": "mail EG"}' )))
        assert not result.ok and "hello-world: hello" in result.text
        assert not h.kit.transport.box( "yolanda", "new" )
    finally:
        h.close()


def test_no_reply_is_a_failure_that_names_the_sent_task() -> None:
    h = PostOfficeHarness()
    try:
        h.clock.on_sleep = lambda: None                              # nobody works hello-world's inbox
        ops, _ = wrap( MessageOperation( h.kit.dispatcher, h.kit.replies, { "hello-world": [ "hello" ]}, wait_seconds=3 ))
        result = asyncio.run( ops.execute( envelope( '{"sheila": "message", "to": "hello-world", "task_type": "hello"}' )))
        assert not result.ok and "no reply" in result.text and result.details[ "sent_task_id" ]
    finally:
        h.close()


def test_the_card_builds_operations_in_front_of_pi() -> None:
    h = PostOfficeHarness()
    try:
        base = factory.JsonAgentRegistry( factory.REGISTRY ).card( "sheila" )
        home = Path( tempfile.mkdtemp()) / "pi-home"
        card = base.model_copy( update={ "settings": { **base.settings, "pi_home": str( home ),
                                                       "jail_login_file": str( home.parent / "jail.json" )}})
        built = factory.default_builders( None, None, h.dir, None, h.kit.dispatcher, h.kit.replies )[ "pi_coding_agent" ]( card )
        assert isinstance( built, SheilaOperations )
        assert set( built._operations ) == { "status", "bridge", "message", "run_adw" }
        no_po = factory.default_builders( None, None, h.dir )[ "pi_coding_agent" ]( card )
        assert "message" not in no_po._operations                   # no dispatcher, no message operation
    finally:
        h.close()


def test_the_card_takes_bridge_work_and_only_her_machine_lane_serves_it() -> None:
    card = factory.JsonAgentRegistry( factory.REGISTRY ).card( "sheila" )
    assert { "chatgpt-bridge", "toyota-bridge", "sydney-bridge" } <= set( card.accepts_from )
    assert "sheila" in Machine.lane_agents( MACHINES )               # the SQMC lanes leave her out
    bati = Machine.load( MACHINES, "DESKTOP-SHDBATI" )
    assert bati.agent == "bati-desktop" and bati.post_office_agents == ( "sheila", )
    assert Machine.load( MACHINES, "Rosemary46" ).post_office_agents == ()
    adws = card.settings[ "operations" ][ "adws" ]
    assert all( not AdwEntry.model_validate( e ).writes for e in adws.values())   # nothing on her list writes today


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted( globals().items()):
        if name.startswith( "test_" ):
            try:
                fn(); print( "ok  ", name )
            except Exception as error:
                failed += 1; print( "FAIL", name, type( error ).__name__, error )
    sys.exit( 1 if failed else 0 )
