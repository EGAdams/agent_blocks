"""DeployAgent and its uv command adapter, offline. Nothing is deployed.

    cd lancedb_memory
    .venv/bin/python -m pytest -q agent_bridge_worker/tests/test_deploy_agent.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.deploy_process import UvDeployCommands
from agent_bridge_worker.deploy_agent import DeployAgent, DeployCommandFailed
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_comms.tests.fakes import FakeClock


def task( prompt: dict | str, operation: str = "implement" ) -> ClaimedTask:
    mode = "read_only" if operation == "inspect" else "workspace_write"
    return ClaimedTask( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1, agent="deploy",
                        workspace="deploy", operation=operation, approval_mode=mode, sender="eg-chatgpt",
                        prompt=prompt if isinstance( prompt, str ) else json.dumps( prompt ))


class FakeRunning:
    def __init__( self, answer, ticks=0, error=None ):
        self.answer, self.ticks, self.error, self.stopped = answer, ticks, error, False

    def poll( self ):
        if self.ticks > 0:
            self.ticks -= 1
            return None
        if self.error:
            raise DeployCommandFailed( self.error )
        return self.answer

    def stop( self ):
        self.stopped = True


class FakeCommands:
    def __init__( self, answer=None, **kw ):
        self.answer, self.kw, self.calls, self.last = answer, kw, [], None

    def start( self, operation, flags, dry_run ):
        self.calls.append(( operation, flags, dry_run ))
        self.last = FakeRunning( self.answer, **self.kw )
        return self.last


def answer( ok=True, verified=True, line="dashboard: deployed 2 file(s)", **data ) -> dict:
    return { "ok": ok, "verified": verified, "line": line, "data": { "adw_id": "0badc0de", **data }, "adw_id": "0badc0de" }


def run( agent, t, signals=None ):
    signals = list( signals or [])
    return agent.run( t, Path( "/tmp" ), lambda: signals.pop( 0 ) if signals else "continue" )


def test_a_verified_deploy_succeeds_and_carries_the_adw_id():
    commands = FakeCommands( answer( release_id="r1" ))
    out = run( DeployAgent( commands, FakeClock()), task({ "deploy": "deploy", "project": "dashboard", "reason": "blink" }))
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "adw_id" ] == "0badc0de"
    operation, flags, dry = commands.calls[ 0 ]
    assert operation == "deploy" and flags[ "project" ] == "dashboard" and flags[ "reason" ] == "blink" and not dry
    assert flags[ "task-id" ] == TASK_ID and flags[ "client" ] == "eg-chatgpt"


def test_an_unverified_result_is_a_failure():
    out = run( DeployAgent( FakeCommands( answer( ok=False, line="checks failed" )), FakeClock()),
               task({ "deploy": "deploy", "project": "dashboard", "reason": "x" }))
    assert out.kind == OutcomeKind.FAILED and not out.retryable


def test_a_dry_run_needs_no_reason_and_succeeds_unverified():
    commands = FakeCommands( answer( verified=False, line="dry run: would deploy 2 file(s)" ))
    out = run( DeployAgent( commands, FakeClock()), task({ "deploy": "deploy", "project": "dashboard", "dry_run": True }))
    assert out.kind == OutcomeKind.SUCCEEDED and commands.calls[ 0 ][ 2 ] is True


def test_reads_are_inspect_and_writes_are_implement():
    agent = DeployAgent( FakeCommands( answer()), FakeClock())
    assert run( agent, task({ "deploy": "status", "project": "dashboard" }, "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert run( agent, task({ "deploy": "status", "project": "dashboard" }, "implement" )).kind == OutcomeKind.REFUSED
    assert run( agent, task({ "deploy": "restart", "project": "dashboard" }, "inspect" )).kind == OutcomeKind.REFUSED


def test_bad_requests_are_refused_before_anything_runs():
    commands = FakeCommands( answer())
    agent = DeployAgent( commands, FakeClock())
    for prompt in ( "deploy the dashboard", { "deploy": "format_disk", "project": "x" }, { "deploy": "deploy", "reason": "x" },
                    { "deploy": "deploy", "project": "dashboard" }, { "deploy": "deploy", "project": "../etc", "reason": "x" },
                    { "deploy": "deploy", "project": "dashboard", "reason": "x", "command": "rm -rf /" },
                    { "deploy": "restart", "project": "dashboard", "unit": "a b; reboot" }):
        assert run( agent, task( prompt )).kind == OutcomeKind.REFUSED, prompt
    assert commands.calls == []


def test_a_cancel_stops_a_read_but_never_a_change():
    read = FakeCommands( answer(), ticks=3 )
    out = run( DeployAgent( read, FakeClock()), task({ "deploy": "logs", "project": "dashboard" }, "inspect" ), [ "continue", "cancel" ])
    assert out.kind == OutcomeKind.CANCELLED and read.last.stopped
    change = FakeCommands( answer(), ticks=3 )
    out = run( DeployAgent( change, FakeClock()), task({ "deploy": "restart", "project": "dashboard" }), [ "continue", "lost", "lost" ])
    assert out.kind == OutcomeKind.SUCCEEDED and not change.last.stopped


def test_no_outcome_is_a_failure():
    out = run( DeployAgent( FakeCommands( error="exit status 1, no outcome file" ), FakeClock()),
               task({ "deploy": "health", "project": "dashboard" }, "inspect" ))
    assert out.kind == OutcomeKind.FAILED and "no outcome" in out.summary


def test_argv_keeps_every_value_a_value():
    argv = UvDeployCommands( Path( "/uv" ), Path( "/adw.py" ), Path( "/" )).argv( "deploy", { "reason": "--rm" }, True, "/tmp/r" )
    assert argv[ 3: ] == [ "/adw.py", "deploy.deploy", "--reason=--rm", "--dry-run", "--json-out=/tmp/r" ]
    try:
        UvDeployCommands( Path( "/uv" ), Path( "/adw.py" ), Path( "/" )).argv( "shell", {}, False, "/tmp/r" )
    except DeployCommandFailed:
        pass
    else:
        raise AssertionError( "an unknown operation must not become argv" )
