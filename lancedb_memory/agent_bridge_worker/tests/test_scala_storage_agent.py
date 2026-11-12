"""ScalaStorageAgent and its uv command adapter, offline. Nothing is filed.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_scala_storage_agent
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.scala_storage_process import UvScalaStorageCommands
from agent_bridge_worker.models import OutcomeKind
from agent_bridge_worker.scala_storage_agent import ScalaStorageAgent
from agent_bridge_worker.tests.test_deploy_agent import FakeCommands, run, task
from agent_bridge_worker.tests.test_bridge_worker import TASK_ID
from agent_comms.tests.fakes import FakeClock

SCAN = "scan_01M787R1STEA7N30DM8PG13MSZ"


class Commands( FakeCommands ):
    def start( self, operation, flags ):
        return super().start( operation, flags, False )


def ans( ok=True, verified=True, retryable=False, line="copied", **data ) -> dict:
    return { "ok": ok, "verified": verified, "retryable": retryable, "line": line, "data": data, "adw_id": "0badc0de" }


def test_store_is_implement_and_becomes_flags():
    c = Commands( ans( sha256="9e0e", filename="2026-11-12_home_9e0e779533b6.jpg" ))
    out = run( ScalaStorageAgent( c, FakeClock()), task({ "storage": "store", "scan_id": SCAN, "machine": "BATI",
                                                           "folder": "insurance/home" }))
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "adw_id" ] == "0badc0de" and out.details[ "sha256" ] == "9e0e"
    op, flags, _ = c.calls[ 0 ]
    assert op == "store" and flags == { "scan-id": SCAN, "machine": "BATI", "folder": "insurance/home",
                                        "task-id": TASK_ID, "client": "eg-chatgpt" }


def test_reads_are_inspect_and_store_is_not():
    c = Commands( ans())
    assert run( ScalaStorageAgent( c, FakeClock()), task({ "storage": "targets" }, "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert run( ScalaStorageAgent( c, FakeClock()), task({ "storage": "store", "scan_id": SCAN, "machine": "BATI",
                                                           "folder": "taxes" }, "inspect" )).kind == OutcomeKind.REFUSED
    assert run( ScalaStorageAgent( c, FakeClock()), task({ "storage": "folders", "machine": "R46" })).kind == OutcomeKind.REFUSED
    assert len( c.calls ) == 1


def test_bad_values_never_reach_the_adw():
    c = Commands( ans())
    agent = ScalaStorageAgent( c, FakeClock())
    for bad in ({ "storage": "store", "scan_id": SCAN, "machine": "BATI", "folder": "../../etc" },
                { "storage": "store", "scan_id": SCAN, "machine": "BATI", "folder": "--json-out=/x" },
                { "storage": "store", "scan_id": "scan_x; rm -rf", "machine": "BATI", "folder": "taxes" },
                { "storage": "store", "scan_id": SCAN, "machine": "BATI" },
                { "storage": "store", "scan_id": SCAN, "machine": "BATI", "folder": "taxes", "path": "/etc" },
                { "storage": "move", "scan_id": SCAN, "machine": "BATI", "folder": "taxes" },
                { "storage": "delete" }, "not json" ):
        assert run( agent, task( bad )).kind == OutcomeKind.REFUSED, bad
    assert c.calls == []


def test_unreachable_is_failed_and_retryable_with_details():
    c = Commands( ans( ok=False, verified=False, retryable=True, line="BATI is unreachable", pending_copy="/x" ))
    out = run( ScalaStorageAgent( c, FakeClock()), task({ "storage": "store", "scan_id": "last", "machine": "bati",
                                                           "folder": "insurance/home" }))
    assert out.kind == OutcomeKind.FAILED and out.retryable and out.details[ "pending_copy" ] == "/x"


def test_a_started_store_ignores_cancel_but_a_read_stops():
    c = Commands( ans(), ticks=2 )
    out = run( ScalaStorageAgent( c, FakeClock()), task({ "storage": "store", "scan_id": SCAN, "machine": "BATI",
                                                           "folder": "taxes" }), [ "continue", "cancel", "cancel" ])
    assert out.kind == OutcomeKind.SUCCEEDED and not c.last.stopped
    c = Commands( ans(), ticks=2 )
    out = run( ScalaStorageAgent( c, FakeClock()), task({ "storage": "health", "machine": "SQMC" }, "inspect" ),
               [ "continue", "cancel" ])
    assert out.kind == OutcomeKind.CANCELLED and c.last.stopped


def test_argv_is_flag_equals_value():
    argv = UvScalaStorageCommands( Path( "/uv" ), Path( "/a.py" ), Path( "/" )).argv( "store", { "folder": "-x" }, "/r" )
    assert argv == [ "/uv", "run", "--quiet", "/a.py", "storage.store", "--folder=-x", "--json-out=/r" ]


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            fn()
            print( "ok  ", name )
    print( "all passed" )
