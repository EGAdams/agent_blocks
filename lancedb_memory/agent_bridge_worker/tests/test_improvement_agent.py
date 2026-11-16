"""ImprovementAgent and its process adapter, offline. No cycle runs and no ADW is started.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_improvement_agent
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.improvement_process import ProcessImprovementCommands
from agent_bridge_worker.improvement_agent import ImprovementAgent
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.tests.test_deploy_agent import FakeCommands, run, task
from agent_bridge_worker.tests.test_bridge_worker import TASK_ID
from agent_comms.tests.fakes import FakeClock

CYCLE = "sydney-20261115T210000Z-abc123"


class Commands( FakeCommands ):
    def start( self, operation, flags ):
        return super().start( operation, flags, False )


def ans( ok=True, verified=True, line="cycle activated", **data ) -> dict:
    return { "ok": ok, "verified": verified, "retryable": False, "line": line, "data": data, "adw_id": "0badc0de" }


def as_sender( t: ClaimedTask, sender: str ) -> ClaimedTask:
    return t.model_copy( update={ "sender": sender })


def test_start_is_implement_and_the_task_id_is_the_request_key():
    c = Commands( ans( cycle_id=CYCLE, state="activated" ))
    out = run( ImprovementAgent( c, FakeClock()), task({ "improvement": "start", "target": "sydney", "environment": "pilot" }))
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "cycle_id" ] == CYCLE
    op, flags, _ = c.calls[ 0 ]
    assert op == "start" and flags == { "target": "sydney", "environment": "pilot", "requested-by": "eg-chatgpt",
                                        "request-key": TASK_ID, "task-id": TASK_ID }


def test_reads_are_inspect_and_writes_are_implement():
    c = Commands( ans())
    assert run( ImprovementAgent( c, FakeClock()), task({ "improvement": "status", "cycle_id": CYCLE }, "inspect" )).kind \
        == OutcomeKind.SUCCEEDED
    assert run( ImprovementAgent( c, FakeClock()), task({ "improvement": "status", "cycle_id": CYCLE })).kind \
        == OutcomeKind.REFUSED
    assert run( ImprovementAgent( c, FakeClock()), task({ "improvement": "start" }, "inspect" )).kind == OutcomeKind.REFUSED


def test_decide_takes_identity_from_the_bridge_not_the_prompt():
    c = Commands( ans( line="decision recorded" ))
    out = run( ImprovementAgent( c, FakeClock()), task({ "improvement": "decide", "cycle_id": CYCLE, "approve": True }))
    assert out.kind == OutcomeKind.SUCCEEDED
    _, flags, _ = c.calls[ 0 ]
    assert flags[ "approver" ] == "eg-chatgpt" and flags[ "key" ] == TASK_ID and flags[ "approve" ] == ""
    refused = run( ImprovementAgent( c, FakeClock()), task({ "improvement": "decide", "cycle_id": CYCLE, "approve": True,
                                                              "approver": "EG" }))
    assert refused.kind == OutcomeKind.REFUSED and "does not take" in refused.summary


def test_eg_s_own_chatgpt_connector_decides_as_eg():
    c = Commands( ans( line="decision recorded" ))
    out = run( ImprovementAgent( c, FakeClock()), as_sender( task({ "improvement": "decide", "cycle_id": CYCLE,
                                                                   "approve": True, "reason": "looks right" }),
                                                             "chatgpt-bridge" ))
    assert out.kind == OutcomeKind.SUCCEEDED
    _, flags, _ = c.calls[ 0 ]
    assert flags[ "approver" ] == "EG" and flags[ "reason" ] == "looks right (via chatgpt-bridge)"
    run( ImprovementAgent( c, FakeClock()), as_sender( task({ "improvement": "decide", "cycle_id": CYCLE, "approve": True }),
                                                       "toyota-bridge" ))
    assert c.calls[ 1 ][ 1 ][ "approver" ] == "toyota-bridge"


def test_the_target_may_read_but_not_judge_her_own_cycle():
    c = Commands( ans())
    agent = ImprovementAgent( c, FakeClock())
    for request in ({ "improvement": "decide", "cycle_id": CYCLE, "approve": True },
                    { "improvement": "rollback", "cycle_id": CYCLE },
                    { "improvement": "start", "target": "sydney" }):
        out = run( agent, as_sender( task( request ), "sydney-bridge" ))
        assert out.kind == OutcomeKind.REFUSED and "her own" in out.summary, request
    assert run( agent, as_sender( task({ "improvement": "proposal", "cycle_id": CYCLE }, "inspect" ), "sydney-bridge" )).kind \
        == OutcomeKind.SUCCEEDED
    assert len( c.calls ) == 1


def test_values_are_checked_before_they_become_flags():
    c = Commands( ans())
    agent = ImprovementAgent( c, FakeClock())
    for bad in ({ "improvement": "status", "cycle_id": "--fail-at=gates" },
                { "improvement": "start", "environment": "prod" },
                { "improvement": "start", "proposal_file": "/etc/passwd" },
                { "improvement": "decide", "cycle_id": CYCLE, "approve": "yes" },
                { "improvement": "rollback", "cycle_id": CYCLE, "reason": "x" * 600 },
                { "improvement": "launch" }):
        op = "inspect" if bad[ "improvement" ] == "status" else "implement"
        assert run( agent, task( bad, op )).kind == OutcomeKind.REFUSED, bad
    assert c.calls == []


def test_a_bridge_cancel_during_a_cycle_becomes_a_cancel_request():
    c = Commands( ans( ok=True, line="cycle cancelled", state="cancelled" ), ticks=3 )
    out = run( ImprovementAgent( c, FakeClock()), task({ "improvement": "start", "cycle_id": CYCLE }),
               signals=[ "continue", "cancel", "cancel" ])
    ops = [ op for op, _, _ in c.calls ]
    assert ops.count( "cancel" ) == 1 and ops[ 0 ] == "start"
    assert out.kind == OutcomeKind.SUCCEEDED


def test_a_failed_cycle_is_failed_not_success():
    c = Commands( ans( ok=False, verified=False, line="cycle rejected: held_out" ))
    out = run( ImprovementAgent( c, FakeClock()), task({ "improvement": "start" }))
    assert out.kind == OutcomeKind.FAILED and "held_out" in out.summary


def test_argv_uses_flag_equals_value_and_bare_switches():
    commands = ProcessImprovementCommands( Path( "/py" ), Path( "/c.py" ), Path( "/o.py" ), Path( "/" ))
    assert commands.argv( "start", { "target": "sydney", "request-key": "-x" }, "/r" ) == \
        [ "/py", "/c.py", "--request-key=-x", "--target=sydney", "--json-out=/r" ]
    assert commands.argv( "decide", { "approve": "", "cycle-id": CYCLE }, "/r" ) == \
        [ "/py", "/o.py", "decide", "--approve", f"--cycle-id={ CYCLE }", "--json-out=/r" ]


if __name__ == "__main__":
    tests = [ v for k, v in sorted( globals().items()) if k.startswith( "test_" )]
    for test in tests:
        test()
    print( f"{ len( tests )} passed" )
