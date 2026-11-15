"""MachineSupervisorAgent and its uv command adapter, offline. No machine is reached and no todo is given.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_machine_supervisor_agent
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.machine_supervisor_process import UvMachineSupervisorCommands
from agent_bridge_worker.machine_supervisor_agent import MachineSupervisorAgent
from agent_bridge_worker.models import OutcomeKind
from agent_bridge_worker.tests.test_deploy_agent import FakeCommands, run, task
from agent_bridge_worker.tests.test_bridge_worker import TASK_ID
from agent_comms.tests.fakes import FakeClock

MESSAGE = "20261114T120001Z_6eee5cde_from_supervisor-sqmc"


class Commands( FakeCommands ):
    def start( self, operation, flags ):
        return super().start( operation, flags, False )


def ans( ok=True, verified=True, retryable=False, line="3 machines", **data ) -> dict:
    return { "ok": ok, "verified": verified, "retryable": retryable, "line": line, "data": data, "adw_id": "0badc0de" }


def test_reading_a_mailbox_is_inspect_and_becomes_flags():
    c = Commands( ans( line="BATI: 2 new", counts={ "new": 2 }))
    out = run( MachineSupervisorAgent( c, FakeClock()), task({ "supervisor": "mailbox", "machine": "BATI", "box": "new" }, "inspect" ))
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "adw_id" ] == "0badc0de" and '"new": 2' in out.body
    op, flags, _ = c.calls[ 0 ]
    assert op == "mailbox" and flags == { "machine": "BATI", "box": "new", "task-id": TASK_ID, "client": "eg-chatgpt" }
    assert run( MachineSupervisorAgent( c, FakeClock()), task({ "supervisor": "read", "machine": "SQMC", "id": MESSAGE },
                                                              "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert c.calls[ 1 ][ 1 ][ "id" ] == MESSAGE


def test_a_todo_is_implement_and_its_text_is_one_value():
    c = Commands( ans( line="todo queued on R46" ))
    text = "Find out why --the scanner; restarted `last night` && say so."
    out = run( MachineSupervisorAgent( c, FakeClock()), task({ "supervisor": "todo", "machine": "R46", "text": text }))
    assert out.kind == OutcomeKind.SUCCEEDED
    op, flags, _ = c.calls[ 0 ]
    assert op == "todo" and flags == { "machine": "R46", "text": text, "task-id": TASK_ID, "client": "eg-chatgpt" }


def test_reads_are_inspect_and_a_todo_is_not():
    c = Commands( ans())
    agent = MachineSupervisorAgent( c, FakeClock())
    assert run( agent, task({ "supervisor": "machines" }, "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert run( agent, task({ "supervisor": "todo", "machine": "BATI", "text": "x" }, "inspect" )).kind == OutcomeKind.REFUSED
    assert run( agent, task({ "supervisor": "status", "machine": "BATI" })).kind == OutcomeKind.REFUSED
    assert len( c.calls ) == 1


def test_bad_values_never_reach_the_adw():
    c = Commands( ans())
    agent = MachineSupervisorAgent( c, FakeClock())
    for bad, op in (({ "supervisor": "mailbox", "machine": "../etc" }, "inspect" ),
                    ({ "supervisor": "mailbox", "machine": "--json-out=/x" }, "inspect" ),
                    ({ "supervisor": "mailbox", "machine": "BATI", "box": "../../home" }, "inspect" ),
                    ({ "supervisor": "read", "machine": "BATI", "id": "../../.ssh/id_ed25519" }, "inspect" ),
                    ({ "supervisor": "read", "machine": "BATI" }, "inspect" ),
                    ({ "supervisor": "status" }, "inspect" ),
                    ({ "supervisor": "status", "machine": "BATI", "path": "/etc" }, "inspect" ),
                    ({ "supervisor": "machines", "text": "x" }, "inspect" ),
                    ({ "supervisor": "todo", "machine": "BATI" }, "implement" ),
                    ({ "supervisor": "todo", "machine": "BATI", "text": "   " }, "implement" ),
                    ({ "supervisor": "todo", "machine": "BATI", "text": "x" * 4001 }, "implement" ),
                    ({ "supervisor": "todo", "machine": "BATI", "text": "x", "box": "new" }, "implement" ),
                    ({ "supervisor": "todo", "text": "x" }, "implement" ),
                    ({ "supervisor": "restart", "machine": "BATI" }, "implement" ),
                    ( "not json", "inspect" )):
        assert run( agent, task( bad, op )).kind == OutcomeKind.REFUSED, bad
    assert c.calls == []


def test_a_machine_that_is_off_is_failed_with_its_line():
    c = Commands( ans( ok=False, verified=False, retryable=True, line="R46 did not answer; it may be switched off" ))
    out = run( MachineSupervisorAgent( c, FakeClock()), task({ "supervisor": "status", "machine": "R46" }, "inspect" ))
    assert out.kind == OutcomeKind.FAILED and out.retryable and "switched off" in out.summary


def test_a_todo_being_handed_over_ignores_cancel_but_a_read_stops():
    c = Commands( ans(), ticks=2 )
    out = run( MachineSupervisorAgent( c, FakeClock()), task({ "supervisor": "todo", "machine": "BATI", "text": "x" }),
               [ "continue", "cancel", "cancel" ])
    assert out.kind == OutcomeKind.SUCCEEDED and not c.last.stopped
    c = Commands( ans(), ticks=2 )
    out = run( MachineSupervisorAgent( c, FakeClock()), task({ "supervisor": "status", "machine": "SQMC" }, "inspect" ),
               [ "continue", "cancel" ])
    assert out.kind == OutcomeKind.CANCELLED and c.last.stopped


def test_argv_picks_the_adw_and_is_flag_equals_value():
    commands = UvMachineSupervisorCommands( Path( "/uv" ), Path( "/box.py" ), Path( "/todo.py" ), Path( "/" ))
    assert commands.argv( "mailbox", { "machine": "BATI", "box": "new" }, "/r" ) == [
        "/uv", "run", "--quiet", "/box.py", "mailbox", "--box=new", "--machine=BATI", "--json-out=/r" ]
    assert commands.argv( "todo", { "machine": "R46", "text": "--work now" }, "/r" ) == [
        "/uv", "run", "--quiet", "/todo.py", "--machine=R46", "--text=--work now", "--json-out=/r" ]


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            fn()
            print( "ok  ", name )
    print( "all passed" )
