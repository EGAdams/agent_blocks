"""The fine-grained operations, alone and composed, and one real ADW run.

    cd lancedb_memory
    .venv/bin/python -m agent_comms.tests.test_operations
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.executors.basic import ScriptedExecutor
from agent_comms.factory import WORKSPACE, build_post_office
from agent_comms.models import TaskResult, TaskStatus
from agent_comms.operations.base import OperationOutcome, OperationSequence
from agent_comms.operations.catalog import operations
from agent_comms.tests.fakes import FakeClock


def _kit():
    root = Path( tempfile.mkdtemp( prefix="po_ops_" ))
    return build_post_office( root=root, launcher=RecordingLauncher(), clock=FakeClock()), root


def test_every_operation_has_its_own_adw() -> None:
    kit, root = _kit()
    try:
        names   = set( operations( kit ))
        on_disk = { p.stem.removeprefix( "adw_op_" ) for p in ( WORKSPACE / "adws" ).glob( "adw_op_*.py" )}
        assert names == on_disk, ( names ^ on_disk )
    finally:
        shutil.rmtree( root, ignore_errors=True )


def test_operations_compose_into_delegation() -> None:
    kit, root = _kit()
    try:
        ops     = operations( kit )
        hello   = ops[ "hello_world" ].run({})
        assert hello.line == "Hello World"
        assert "sonnet" in ops[ "discover_agents" ].run({}).data[ "agents" ]
        assert "build" in ops[ "read_capabilities" ].run({ "agent": "opus" }).line
        outcome = ops[ "delegate_task" ].run({ "sender": "sydney", "text": "opus: plan the rollout" })
        assert outcome.ok and outcome.data[ "asynchronous" ], outcome
        assert [ n for n, _ in ops[ "delegate_task" ].trail ] == [ "route_request", "validate_task", "send_message" ]
        kit.overrides[ "opus" ] = ScriptedExecutor([ TaskResult( ok=True, text="the plan", performer="x" )])
        settled = ops[ "execute_task" ].run({ "agent": "opus" })
        assert "succeeded" in settled.line
        got = ops[ "receive_message" ].run({ "agent": "sydney" })
        assert got.line == "From opus: the plan"
        refused = ops[ "delegate_task" ].run({ "sender": "sydney", "text": "mechanic: plan a party" })
        assert not refused.ok and "can do: diagnose, repair" in refused.line
        assert [ n for n, _ in ops[ "delegate_task" ].trail ] == [ "route_request", "validate_task" ]
    finally:
        shutil.rmtree( root, ignore_errors=True )


def test_a_sequence_stops_at_the_first_failure_and_merges_data() -> None:
    class Step:
        def __init__( self, name, ok, data ):
            self.name, self.description, self.performer = name, name, "test"
            self._ok, self._data = ok, data

        def run( self, args ):
            return OperationOutcome( ok=self._ok, line=self.name, data={ **self._data, "saw": sorted( args )})

    seq = OperationSequence( "s", "s", [ Step( "a", True, { "x": 1 }), Step( "b", False, {}), Step( "c", True, {})])
    out = seq.run({ "start": 0 })
    assert not out.ok and out.line == "b" and out.data[ "saw" ] == [ "saw", "start", "x" ]


def test_retry_needs_confirmation_for_work_that_changes_files() -> None:
    kit, root = _kit()
    try:
        ops = operations( kit )
        kit.overrides[ "sonnet" ] = ScriptedExecutor([ TaskResult( ok=False, text="tests failed", performer="x" )])
        sent = ops[ "send_and_capture" ].run({ "sender": "sydney", "recipient": "sonnet", "task_type": "build",
                                               "text": "add X" })
        ops[ "execute_task" ].run({ "agent": "sonnet" })
        task_id = sent.data[ "task_id" ]
        assert kit.board.task( task_id ).status is TaskStatus.FAILED
        assert "only sent again when you confirm" in ops[ "retry_task" ].run({ "task_id": task_id }).line
        again = ops[ "retry_task" ].run({ "task_id": task_id, "confirm": True })
        assert again.ok and kit.board.task( again.data[ "task_id" ]).parent_id == task_id
        diagnosis = ops[ "request_mechanic" ].run({ "sender": "sydney", "task_id": task_id, "wait_seconds": 0 })
        assert diagnosis.ok and diagnosis.data[ "pending" ]
    finally:
        shutil.rmtree( root, ignore_errors=True )


def test_hello_world_adw_runs_as_a_real_session() -> None:
    if not all( importlib.util.find_spec( m ) for m in ( "yaml", "rich", "dotenv" )):
        print( "    (skipped: this interpreter lacks the SSSF modules' yaml/rich/dotenv)" )
        return
    root = Path( tempfile.mkdtemp( prefix="po_adw_" ))
    try:
        line = root / "line.txt"
        done = subprocess.run([ sys.executable, "adws/adw_op_hello_world.py", "--message-out", str( line )],
                              cwd=WORKSPACE, capture_output=True, text=True, timeout=120,
                              env={ **__import__( "os" ).environ, "AGENT_POST_OFFICE_ROOT": str( root )})
        assert done.returncode == 0, done.stdout[ -2000: ] + done.stderr[ -2000: ]
        assert line.read_text().strip() == "Hello World"
    finally:
        shutil.rmtree( root, ignore_errors=True )


def test_every_adw_parses_its_own_flags() -> None:
    if not all( importlib.util.find_spec( m ) for m in ( "yaml", "rich", "dotenv" )):
        print( "    (skipped: this interpreter lacks the SSSF modules' yaml/rich/dotenv)" )
        return
    for script in sorted(( WORKSPACE / "adws" ).glob( "adw_op_*.py" )):
        done = subprocess.run([ sys.executable, str( script ), "--help" ], cwd=WORKSPACE,
                              capture_output=True, text=True, timeout=60 )
        assert done.returncode == 0, f"{ script.name }: { done.stderr[ -600: ]}"


if __name__ == "__main__":
    tests = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
    for test in tests:
        test()
        print( f"ok  { test.__name__ }" )
    print( f"{ len( tests ) } passed" )
