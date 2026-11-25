"""TriageAgent and its uv command adapter, offline. No ledger is read and no fix is recorded.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_triage_agent
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.triage_process import UvTriageCommands
from agent_bridge_worker.models import OutcomeKind
from agent_bridge_worker.tests.test_bridge_worker import TASK_ID
from agent_bridge_worker.tests.test_deploy_agent import FakeCommands, run, task
from agent_bridge_worker.triage_agent import TriageAgent
from agent_comms.tests.fakes import FakeClock


class Commands( FakeCommands ):
    def start( self, operation, flags ):
        return super().start( operation, flags, False )


def ans( ok=True, verified=True, line="2 live failure cluster(s)", **data ) -> dict:
    return { "ok": ok, "verified": verified, "retryable": False, "line": line, "data": data, "adw_id": "0badc0de" }


def test_status_is_inspect_and_returns_the_line_and_data():
    c = Commands( ans( counts={ "live": 2 }))
    out = run( TriageAgent( c, FakeClock()), task({ "triage": "status" }, "inspect" ))
    assert out.kind == OutcomeKind.SUCCEEDED and out.summary.startswith( "2 live" ) and '"live": 2' in out.body
    assert c.calls[ 0 ][ :2 ] == ( "status", { "task-id": TASK_ID, "client": "eg-chatgpt" })


def test_clusters_and_cluster_take_checked_values():
    c = Commands( ans())
    agent = TriageAgent( c, FakeClock())
    assert run( agent, task({ "triage": "clusters", "state": "open" }, "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert run( agent, task({ "triage": "cluster", "cluster": "3f2a9c" }, "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert c.calls[ 0 ][ 1 ][ "state" ] == "open" and c.calls[ 1 ][ 1 ][ "cluster" ] == "3f2a9c"


def test_a_fix_is_implement_and_its_text_is_one_value():
    c = Commands( ans( line="Recorded the fix" ))
    out = run( TriageAgent( c, FakeClock()), task({ "triage": "fix", "cluster": "3f2a9c",
                                                    "fix": "7fa5717  --unit optional;\n `rm -rf`" }))
    assert out.kind == OutcomeKind.SUCCEEDED
    op, flags, _ = c.calls[ 0 ]
    assert op == "fix" and flags[ "fix" ] == "7fa5717 --unit optional; `rm -rf`" and flags[ "cluster" ] == "3f2a9c"


def test_reads_are_inspect_and_writes_are_not():
    c = Commands( ans())
    agent = TriageAgent( c, FakeClock())
    assert run( agent, task({ "triage": "status" })).kind == OutcomeKind.REFUSED
    assert run( agent, task({ "triage": "reroute", "cluster": "3f2a" }, "inspect" )).kind == OutcomeKind.REFUSED
    assert c.calls == []


def test_bad_values_never_reach_the_adw():
    c = Commands( ans())
    agent = TriageAgent( c, FakeClock())
    for bad, op in (({ "triage": "cluster" }, "inspect" ),
                    ({ "triage": "cluster", "cluster": "../x" }, "inspect" ),
                    ({ "triage": "cluster", "cluster": "--json-out=/x" }, "inspect" ),
                    ({ "triage": "cluster", "cluster": "abc" }, "inspect" ),
                    ({ "triage": "clusters", "state": "deleted" }, "inspect" ),
                    ({ "triage": "status", "path": "/etc" }, "inspect" ),
                    ({ "triage": "fix", "cluster": "3f2a9c" }, "implement" ),
                    ({ "triage": "fix", "cluster": "3f2a9c", "fix": "ok" }, "implement" ),
                    ({ "triage": "fix", "cluster": "3f2a9c", "fix": "x" * 401 }, "implement" ),
                    ({ "triage": "fix", "fix": "abc123 something" }, "implement" ),
                    ({ "triage": "reroute", "cluster": "3f2a9c", "fix": "abc123 x" }, "implement" ),
                    ({ "triage": "resend", "cluster": "3f2a9c" }, "implement" ),
                    ( "not json", "inspect" )):
        assert run( agent, task( bad, op )).kind == OutcomeKind.REFUSED, bad
    assert c.calls == []


def test_a_refused_adw_answer_is_failed_with_its_line():
    c = Commands( ans( ok=False, verified=False, line="REFUSED: no single cluster matches '3f2a'" ))
    out = run( TriageAgent( c, FakeClock()), task({ "triage": "cluster", "cluster": "3f2a" }, "inspect" ))
    assert out.kind == OutcomeKind.FAILED and "no single cluster" in out.summary


def test_argv_picks_the_adw_and_is_flag_equals_value():
    commands = UvTriageCommands( Path( "/uv" ), Path( "/status.py" ), Path( "/fix.py" ), Path( "/" ))
    assert commands.argv( "clusters", { "state": "open" }, "/r" ) == [
        "/uv", "run", "--quiet", "/status.py", "clusters", "--state=open", "--json-out=/r" ]
    assert commands.argv( "fix", { "cluster": "3f2a", "fix": "--x y" }, "/r" ) == [
        "/uv", "run", "--quiet", "/fix.py", "fix", "--cluster=3f2a", "--fix=--x y", "--json-out=/r" ]


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            fn()
            print( "ok  ", name )
    print( "all passed" )
