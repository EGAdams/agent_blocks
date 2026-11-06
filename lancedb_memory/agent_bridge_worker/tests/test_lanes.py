"""Worker lanes, offline: the lane rules, the gateway's `?skip=` wire format, and two workers racing one queue.

    cd lancedb_memory
    .venv/bin/python -B -m agent_bridge_worker.tests.test_lanes
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.http_gateway import HttpTaskGateway
from agent_bridge_worker.lanes import WorkerLane
from agent_bridge_worker.models import ClaimedTask
from agent_bridge_worker.policy import ScopedPolicy

CODING = [ "letta_code", "agent_blocks_dev", "digi", "bridge_sandbox" ]
GENERAL, EXPRESS = WorkerLane( name="general" ), WorkerLane( name="express" )


# -- the lane rules ------------------------------------------------------------

def test_general_lane_is_the_old_single_worker() -> None:
    assert GENERAL.skips( CODING ) == []
    assert GENERAL.takes_coding_writes
    assert GENERAL.write_workspaces( CODING, { "notes" } ) == frozenset( CODING ) | { "notes" }
    assert GENERAL.unit == "agent-bridge-worker.service"
    assert GENERAL.poll_state_path.name == "poll_state.json" and GENERAL.health_path.name == "worker_health.json"


def test_express_lane_passes_over_coding_writes_only() -> None:
    assert EXPRESS.skips( CODING ) == [ f"implement@{ w }" for w in sorted( CODING )]
    assert not EXPRESS.takes_coding_writes
    assert EXPRESS.write_workspaces( CODING, { "notes" } ) == frozenset({ "notes" }), "Notes edits still run on express"
    assert EXPRESS.write_workspaces( CODING, set()) == frozenset()


def test_the_two_lanes_never_share_a_name_unit_or_file() -> None:
    assert GENERAL.unit != EXPRESS.unit
    assert GENERAL.poll_state_path != EXPRESS.poll_state_path and GENERAL.health_path != EXPRESS.health_path
    assert GENERAL.default_worker_id( "box" ) != EXPRESS.default_worker_id( "box" )


def test_express_policy_refuses_a_coding_write_that_slips_through() -> None:
    policy = ScopedPolicy({ "codex-writer": { "implement": True }, "notes": { "inspect": False, "implement": True }},
                          EXPRESS.write_workspaces( CODING, { "notes" } ))
    coding = _task( "t_" + "1" * 24, "codex-writer", "letta_code", "implement", "workspace_write" )
    notes  = _task( "t_" + "2" * 24, "notes", "notes", "implement", "workspace_write" )
    assert "not write-enabled" in policy.refusal( coding )
    assert policy.refusal( notes ) is None


# -- the wire format -----------------------------------------------------------

class FakeGatewayServer:
    """A loopback server that records every request line and answers `next` the way the PHP gateway does."""

    def __init__( self, knows_lanes: bool ) -> None:
        outer = self
        self.paths: list[ str ] = []

        class Handler( BaseHTTPRequestHandler ):
            def do_GET( self ) -> None:                               # noqa: N802
                outer.paths.append( self.path )
                if outer.knows_lanes and "skip=lane-probe" in self.path:
                    self._send( 400, { "error": { "code": "bad_skip", "message": "skip entries look like operation@workspace" }} )
                elif "skip=implement@" in self.path:
                    self._send( 200, { "task_id": "t_" + "9" * 24 } )
                else:
                    self.send_response( 204 )
                    self.end_headers()

            def _send( self, status: int, body: dict ) -> None:
                raw = json.dumps( body ).encode()
                self.send_response( status )
                self.send_header( "Content-Type", "application/json" )
                self.send_header( "Content-Length", str( len( raw )))
                self.end_headers()
                self.wfile.write( raw )

            def log_message( self, *args ) -> None:
                pass

        self.knows_lanes = knows_lanes
        self.server      = HTTPServer(( "127.0.0.1", 0 ), Handler )
        self.url         = f"http://127.0.0.1:{ self.server.server_port }"
        threading.Thread( target=self.server.serve_forever, daemon=True ).start()

    def close( self ) -> None:
        self.server.shutdown()


def test_next_sends_the_skip_list_and_plain_next_sends_none() -> None:
    server = FakeGatewayServer( knows_lanes=True )
    try:
        plain = HttpTaskGateway( server.url, "tok" )
        lane  = HttpTaskGateway( server.url, "tok", skip=EXPRESS.skips( CODING ))
        assert plain.next() is None
        assert lane.next() == "t_" + "9" * 24
        assert server.paths[ 0 ] == "/worker/tasks/next", server.paths
        assert server.paths[ 1 ].startswith( "/worker/tasks/next?skip=implement@agent_blocks_dev,implement@bridge_sandbox" )
    finally:
        server.close()


def test_an_old_gateway_is_detected_and_a_new_one_accepted() -> None:
    new, old = FakeGatewayServer( knows_lanes=True ), FakeGatewayServer( knows_lanes=False )
    try:
        assert HttpTaskGateway( new.url, "tok" ).supports_lanes() is True
        assert HttpTaskGateway( old.url, "tok" ).supports_lanes() is False
    finally:
        new.close(); old.close()


# -- two workers, one queue ----------------------------------------------------

class QueueGateway:
    """The real gateway's queue semantics in memory: FIFO, `skip`, and an atomic claim (the loser gets None, a 409)."""

    def __init__( self, tasks: list[ tuple[ str, str, str ]] ) -> None:         # (id, operation, workspace)
        self._lock   = threading.Lock()
        self.queue   = list( tasks )
        self.claimed: dict[ str, str ] = {}
        self.order:   list[ str ] = []

    def next( self, skip: list[ str ] ) -> str | None:
        with self._lock:
            for task_id, operation, workspace in self.queue:
                if f"{ operation }@{ workspace }" not in skip:
                    return task_id
        return None

    def claim( self, task_id: str, worker: str ) -> bool:
        with self._lock:
            entry = next(( t for t in self.queue if t[ 0 ] == task_id ), None )
            if entry is None:
                return False
            self.queue.remove( entry )
            self.claimed[ task_id ] = worker
            self.order.append( task_id )
            return True


def test_many_workers_racing_for_one_head_task_yield_exactly_one_winner() -> None:
    gateway = QueueGateway([( "t1", "explain", "agent_blocks" )])
    wins: list[ str ] = []
    start = threading.Barrier( 12 )

    def racer( n: int ) -> None:
        start.wait()
        if gateway.claim( "t1", f"w{ n }" ):
            wins.append( f"w{ n }" )

    threads = [ threading.Thread( target=racer, args=( n, )) for n in range( 12 )]
    [ t.start() for t in threads ]; [ t.join() for t in threads ]
    assert len( wins ) == 1 and gateway.claimed[ "t1" ] == wins[ 0 ]


def test_express_keeps_serving_diagnostics_while_the_general_worker_is_deep_in_a_coding_write() -> None:
    gateway = QueueGateway([( "w1", "implement", "letta_code" ), ( "w2", "implement", "letta_code" ),
                            ( "d1", "inspect", "machine_ops" ), ( "d2", "explain", "object_trace" ),
                            ( "n1", "implement", "notes" )])
    general_skip, express_skip = GENERAL.skips( CODING ), EXPRESS.skips( CODING )
    # general takes the oldest task: the first coding write, and is then busy with it for "minutes"
    head = gateway.next( general_skip )
    assert head == "w1" and gateway.claim( head, "general" )
    # express never sees the second coding write: it is offered the diagnostics, in order, then the Notes edit
    served = []
    while ( task := gateway.next( express_skip )) is not None:
        assert gateway.claim( task, "express" )
        served.append( task )
    assert served == [ "d1", "d2", "n1" ], served
    assert [ t[ 0 ] for t in gateway.queue ] == [ "w2" ], "the second coding write waits for the general worker, in order"
    # when general finishes w1 it takes w2 next -- coding writes keep their order and never overlap
    assert gateway.claim( gateway.next( general_skip ), "general" )
    assert [ t for t in gateway.order if t.startswith( "w" )] == [ "w1", "w2" ]
    assert all( gateway.claimed[ t ] == "general" for t in ( "w1", "w2" ))
    assert all( gateway.claimed[ t ] == "express" for t in ( "d1", "d2", "n1" ))


def test_without_the_express_lane_a_diagnostic_waits_behind_the_whole_coding_queue() -> None:
    """The failure this exists for: with one lane the diagnostic is last in line."""
    gateway = QueueGateway([( "w1", "implement", "letta_code" ), ( "d1", "inspect", "machine_ops" )])
    assert gateway.claim( gateway.next( GENERAL.skips( CODING )), "general" )
    assert gateway.next( GENERAL.skips( CODING )) == "d1"       # general would reach it only after finishing w1
    assert gateway.next( EXPRESS.skips( CODING )) == "d1"       # express reaches it now


def _task( task_id: str, agent: str, workspace: str, operation: str, mode: str ) -> ClaimedTask:
    return ClaimedTask.model_validate( dict( task_id=task_id, lease_token="L" * 40, lease_expires_at=0, lease_seconds=60,
                                             attempt=1, agent=agent, workspace=workspace, operation=operation,
                                             approval_mode=mode, prompt="x", sender="toyota-bridge" ))


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            fn()
            print( f"ok  { name }" )
    print( "all lane tests passed" )
