"""The bridge worker, offline: a fake gateway, a real post office in a tmpdir.

Only the gateway (HTTP) and Codex (the subprocess) are doubles. The registry is
the real `agent_comms/agents.json`, so the codex card's `accepts_from` and its
read-only capabilities are what is actually tested.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_bridge_worker
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.http_gateway import HttpTaskGateway
from agent_bridge_worker.adapters.post_office_agent import PostOfficeAgent
from agent_bridge_worker.models import Beat, ClaimedTask, OutcomeKind
from agent_bridge_worker.policy import ReadOnlyPolicy, StaticWorkspaces, read_only_operations
from agent_bridge_worker.ports import GatewayError
from agent_bridge_worker.service import BridgeTaskRun
from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.executors.basic import ScriptedExecutor
from agent_comms.factory import build_post_office
from agent_comms.models import TaskRequest, TaskResult
from agent_comms.tests.fakes import FakeClock

TASK_ID = "t_" + "a" * 24
LEASE   = "L" * 40


class FakeGateway:
    """ITaskGatewayClient in memory. Records every call the worker makes."""

    def __init__( self, queued: bool = True, operation: str = "explain", agent: str = "codex",
                  mode: str = "read_only", workspace: str = "agent_blocks" ) -> None:
        self.queued      = queued
        self.claimable   = True
        self.cancel_after: int | None = None    # heartbeats before cancel_requested turns on
        self.lose_after:   int | None = None    # heartbeats before the lease is gone (409)
        self.beats       = 0
        self.events:     list[ tuple[ str, str ]] = []
        self.completed:  dict | None = None
        self.failed:     dict | None = None
        self._claim = dict( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=60, attempt=1,
                            agent=agent, workspace=workspace, operation=operation, approval_mode=mode,
                            prompt="Explain the Toyota voice system's architecture. Change nothing." )

    def next( self ) -> str | None:
        return TASK_ID if self.queued else None

    def claim( self, task_id: str, worker_id: str ) -> ClaimedTask | None:
        return ClaimedTask.model_validate( self._claim ) if self.claimable else None

    def heartbeat( self, task: ClaimedTask ) -> Beat:
        self.beats += 1
        if self.lose_after is not None and self.beats > self.lose_after:
            raise GatewayError( 409, "lease_invalid", "lease expired" )
        cancel = self.cancel_after is not None and self.beats > self.cancel_after
        return Beat( cancel_requested=cancel )

    def event( self, task: ClaimedTask, kind: str, message: str ) -> None:
        self.events.append(( kind, message ))

    def complete( self, task: ClaimedTask, ok: bool, summary: str, body: str, performer: str,
                  details: dict | None = None ) -> str:
        self.completed = dict( ok=ok, summary=summary, body=body, performer=performer, details=details )
        return "succeeded" if ok else "failed"

    def fail( self, task: ClaimedTask, reason: str, retryable: bool, cancelled: bool ) -> str:
        self.failed = dict( reason=reason, retryable=retryable, cancelled=cancelled )
        return "cancelled" if cancelled else "failed"


class Harness:
    """A post office in a tmpdir whose codex runtime answers from a script."""

    def __init__( self, gateway: FakeGateway, answer: TaskResult | None = None, drain: bool = True ) -> None:
        self.dir     = Path( tempfile.mkdtemp( prefix="bridge_worker_" ))
        self.clock   = FakeClock()
        self.gateway = gateway
        self.codex   = ScriptedExecutor([ answer or TaskResult( ok=True, performer="codex:fake",
                                          text="Toyota's voice goes dashboard -> /api/voice -> sidecar.\nDetails..." )],
                                        performer="codex:fake" )
        self.kit = build_post_office( root=self.dir, launcher=RecordingLauncher(), clock=self.clock,
                                      builders={ "codex_cli": lambda card: self.codex },
                                      forward_failures_to_mechanic=False )
        if drain:     # while the bridge waits, Codex's worker runs -- as a woken one would
            self.clock.on_sleep = lambda: asyncio.run( self.kit.worker_for( "codex" ).drain())
        agent = PostOfficeAgent( "chatgpt-bridge", self.kit.dispatcher, self.kit.replies, self.kit.canceller,
                                 self.kit.board, self.kit.event_log, TaskRequest, self.clock, timeout_seconds=120 )
        self.agent = agent
        self.run   = BridgeTaskRun( gateway, ReadOnlyPolicy( read_only_operations( self.kit.registry, "chatgpt-bridge" )),
                                    StaticWorkspaces({ "agent_blocks": self.dir }), { "codex": agent }, agent,
                                    self.clock, "worker-test" )

    def close( self ) -> None:
        shutil.rmtree( self.dir, ignore_errors=True )


def with_harness( gateway: FakeGateway, **kw ):
    h = Harness( gateway, **kw )
    try:
        return h, h.run.run_one()
    finally:
        h.close()


# -- the happy path ------------------------------------------------------------

def test_success_publishes_codex_answer() -> None:
    h, report = with_harness( FakeGateway())
    assert report.outcome is OutcomeKind.SUCCEEDED, report
    assert report.state == "succeeded"
    assert h.gateway.completed[ "ok" ] is True
    assert h.gateway.completed[ "summary" ].startswith( "Toyota's voice goes" )
    assert "Details..." in h.gateway.completed[ "body" ]
    assert h.gateway.completed[ "performer" ] == "codex:fake"
    seen = h.codex.seen[ 0 ]
    assert seen.sender == "chatgpt-bridge" and seen.task_type == "explain"
    assert seen.context[ "bridge_task_id" ] == TASK_ID
    assert report.events, "the on-machine audit trail is forwarded"
    assert not any( "Toyota voice system" in m for _, m in h.gateway.events ), "event lines never carry the prompt"


def test_nothing_queued() -> None:
    h, report = with_harness( FakeGateway( queued=False ))
    assert report.summary == "nothing queued" and report.task_id is None
    assert h.codex.seen == [] and h.gateway.completed is None and h.gateway.failed is None


def test_claim_lost_to_another_worker() -> None:
    g = FakeGateway()
    g.claimable = False
    h, report = with_harness( g )
    assert "claimed by another worker" in report.summary
    assert h.codex.seen == []


# -- the worker's own policy: the second lock ---------------------------------

def _refused( gateway: FakeGateway ) -> tuple:
    h, report = with_harness( gateway )
    assert report.outcome is OutcomeKind.REFUSED, report
    assert h.codex.seen == [], "nothing ran"
    assert h.gateway.failed is not None and h.gateway.failed[ "cancelled" ] is False
    return h, report


def test_write_operation_refused() -> None:
    _, report = _refused( FakeGateway( operation="modify_code" ))
    assert "not read-only" in report.summary


def test_non_read_only_mode_refused() -> None:
    _, report = _refused( FakeGateway( mode="auto_apply" ))
    assert "approval mode" in report.summary


def test_unknown_agent_refused() -> None:
    _, report = _refused( FakeGateway( agent="nobody" ))
    assert "does not run tasks for nobody" in report.summary


def test_agent_that_does_not_accept_the_bridge_refused() -> None:
    # toyota's card does not list chatgpt-bridge, so read_only_operations leaves her out
    _, report = _refused( FakeGateway( agent="toyota" ))
    assert "toyota" in report.summary


def test_unknown_workspace_refused() -> None:
    _, report = _refused( FakeGateway( workspace="etc" ))
    assert "no workspace called etc" in report.summary


# -- cancel and lease ------------------------------------------------------------

def test_cancel_before_start() -> None:
    g = FakeGateway()
    g.cancel_after = 0
    h, report = with_harness( g )
    assert report.outcome is OutcomeKind.CANCELLED and report.state == "cancelled"
    assert h.gateway.failed[ "cancelled" ] is True
    assert h.codex.seen == []


def test_cancel_while_waiting_withdraws_the_queued_task() -> None:
    g = FakeGateway()
    g.cancel_after = 1          # the forced beat in PrepareExecution passes; the next one cancels
    h, report = with_harness( g, drain=False )   # Codex never picks it up
    assert report.outcome is OutcomeKind.CANCELLED, report
    assert "withdrawn before it started" in report.summary
    assert h.gateway.failed[ "cancelled" ] is True


def test_lease_lost_publishes_nothing() -> None:
    g = FakeGateway()
    g.lose_after = 1
    h, report = with_harness( g, drain=False )
    assert report.outcome is OutcomeKind.LEASE_LOST
    assert h.gateway.completed is None and h.gateway.failed is None


def test_agent_failure_is_published_as_failed() -> None:
    h, report = with_harness( FakeGateway(), answer=TaskResult( ok=False, text="codex exited 1", performer="codex:fake" ))
    assert report.outcome is OutcomeKind.FAILED, report
    published = h.gateway.completed or h.gateway.failed
    assert published is not None
    if h.gateway.completed:
        assert h.gateway.completed[ "ok" ] is False


# -- the HTTP client ------------------------------------------------------------

def test_http_gateway_requires_https_and_hides_the_token() -> None:
    for bad in ( "http://example.com/api", "ftp://x" ):
        try:
            HttpTaskGateway( bad, "secret-token-value" )
        except ValueError:
            continue
        raise AssertionError( f"{ bad } should be refused" )
    g = HttpTaskGateway( "https://bridge.example/api/agent-bridge/v1", "secret-token-value" )
    assert "secret-token-value" not in repr( g )
    HttpTaskGateway( "http://127.0.0.1:8099/api/agent-bridge/v1", "t" )


def test_http_gateway_names_itself_to_the_server() -> None:
    import urllib.request
    from agent_bridge_worker.adapters import http_gateway
    seen: list[ urllib.request.Request ] = []

    class Reply:
        status = 204
        def read( self ) -> bytes: return b""
        def __enter__( self ) -> "Reply": return self
        def __exit__( self, *exc: object ) -> None: return None

    def fake_urlopen( request: urllib.request.Request, timeout: float ) -> Reply:
        seen.append( request )
        return Reply()

    real = http_gateway.urllib.request.urlopen
    http_gateway.urllib.request.urlopen = fake_urlopen
    try:
        HttpTaskGateway( "https://bridge.example/api/agent-bridge/v1", "t" ).next()
    finally:
        http_gateway.urllib.request.urlopen = real
    agent = seen[ 0 ].get_header( "User-agent" )
    assert agent == http_gateway.USER_AGENT and "urllib" not in agent.lower()


def test_claimed_task_repr_hides_lease_and_prompt() -> None:
    g = FakeGateway()
    task = g.claim( TASK_ID, "w" )
    assert LEASE not in repr( task ) and "Toyota" not in repr( task )


def run( test ) -> None:
    test()
    print( f"ok  { test.__name__ }" )


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            run( fn )
    print( "all bridge worker tests passed" )
