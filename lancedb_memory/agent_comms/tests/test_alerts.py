"""No silent failures: every failed task reaches its caller, at once.

    cd lancedb_memory
    .venv/bin/python -m agent_comms.tests.test_alerts
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_comms.adapters.launchers import outside_unit_prefix
from agent_comms.executors.basic import ScriptedExecutor
from agent_comms.models import TaskRequest, TaskResult, TaskStatus
from agent_comms.operations.catalog import operations
from agent_comms.tests.fakes import FakeRoleRunner
from agent_comms.tests.test_post_office import Harness, run

REFUSED = "API Error: 400 Claude Code 2.1.250 does not support this model; version 2.1.280 or newer is required."


def test_a_failed_task_raises_an_alert_for_the_agent_who_sent_it() -> None:
    h = Harness( FakeRoleRunner( text=REFUSED, is_error=True ))
    try:
        plan = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan",
                                                   text="plan the task board screen" ))
        h.work( "opus" )
        alerts = h.kit.alerts.pending( "sydney" )
        assert [ a.task_id for a in alerts ] == [ plan.task_id ], alerts
        assert alerts[ 0 ].recipient == "Opus" and "does not support this model" in alerts[ 0 ].error
        assert "plan I sent to Opus did not run" in alerts[ 0 ].spoken()
        assert h.kit.alerts.pending( "toyota" ) == []          # only the caller is told
    finally:
        h.close()


def test_an_unrecoverable_error_fails_on_the_first_attempt() -> None:
    h = Harness( FakeRoleRunner( text=REFUSED, is_error=True ))
    try:
        plan = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan", text="x" ))
        h.work( "opus" )
        task = h.kit.board.task( plan.task_id )
        assert task.status is TaskStatus.FAILED and task.attempts == 1, task   # was 3 runs of the same refusal
    finally:
        h.close()


def test_a_recoverable_error_is_still_retried_without_an_alert() -> None:
    h = Harness()
    try:
        h.kit.overrides[ "sonnet" ] = ScriptedExecutor([ TaskResult( ok=False, text="API Error: 429 rate limited", performer="x" ),
                                                         TaskResult( ok=True, text="fine", performer="x" )], performer="x" )
        ask = h.kit.dispatcher.send( TaskRequest( sender="toyota", recipient="sonnet", task_type="ask",
                                                  text="where?", wait_seconds=0 ))
        h.work( "sonnet" )
        assert h.kit.board.task( ask.task_id ).status is TaskStatus.SUCCEEDED
        assert h.kit.alerts.pending( "toyota" ) == []
    finally:
        h.close()


def test_acknowledging_an_alert_clears_it_once() -> None:
    h = Harness( FakeRoleRunner( text=REFUSED, is_error=True ))
    try:
        h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan", text="x" ))
        h.work( "opus" )
        ops = operations( h.kit )
        first = ops[ "check_alerts" ].run({ "agent": "sydney", "acknowledge": True })
        assert "failed" in first.line and first.data[ "acknowledged" ] == 1, first
        again = ops[ "check_alerts" ].run({ "agent": "sydney" })
        assert again.line == "No failed tasks waiting for sydney.", again
    finally:
        h.close()


def test_a_dead_workers_task_is_swept_and_a_living_one_is_left_alone() -> None:
    h = Harness()
    try:
        build = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="sonnet", task_type="build", text="x" ))
        long  = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="sonnet", task_type="build", text="y" ))
        h.kit.transport.claim( "sonnet", build.task_id )    # its worker was killed by a restart
        h.kit.transport.claim( "sonnet", long.task_id )     # this one's worker is still at it
        with h.kit.liveness.hold( long.task_id ):
            h.kit.sweeper( grace_seconds=-1 ).sweep()
            assert h.kit.board.task( long.task_id ).status is TaskStatus.RUNNING
        assert h.kit.board.task( build.task_id ).status is TaskStatus.FAILED
        alerts = h.kit.alerts.pending( "sydney" )
        assert [ a.task_id for a in alerts ] == [ build.task_id ] and "interrupted" in alerts[ 0 ].error
    finally:
        h.close()


def test_a_swept_plan_goes_back_in_the_queue_and_its_agent_is_woken() -> None:
    h = Harness()
    try:
        plan = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan", text="x" ))
        h.kit.transport.claim( "opus", plan.task_id )
        h.launcher.woken.clear()
        h.kit.sweeper( grace_seconds=-1 ).sweep()
        assert h.kit.board.task( plan.task_id ).status is TaskStatus.QUEUED
        assert h.launcher.woken == [ "opus" ] and h.kit.alerts.pending( "sydney" ) == []
    finally:
        h.close()


def test_a_failed_mechanic_errand_is_escalated_rather_than_dropped() -> None:
    h = Harness()
    try:
        class Broken:
            performer = "python:broken"

            async def execute( self, envelope ):
                raise RuntimeError( "case history unreadable" )

        h.kit.overrides[ "mechanic" ] = Broken()
        h.kit.dispatcher.send( TaskRequest( sender="post-office", recipient="mechanic", task_type="diagnose",
                                            text="look at task x", context={ "task_id": "x" }))
        h.work( "mechanic" )
        alerts = h.kit.alerts.pending( "sydney" )
        assert [ a.recipient for a in alerts ] == [ "the mechanic" ] and "unreadable" in alerts[ 0 ].error, alerts
    finally:
        h.close()


def test_outside_unit_prefix_is_systemd_run_or_nothing() -> None:
    prefix = outside_unit_prefix()
    assert prefix == [] or prefix[ :3 ] == [ "systemd-run", "--user", "--scope" ], prefix


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]

if __name__ == "__main__":
    for test in TESTS:
        run( test )
    print( f"{ len( TESTS ) } passed" )
