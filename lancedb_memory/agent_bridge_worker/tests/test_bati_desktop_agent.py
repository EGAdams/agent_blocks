"""BatiDesktopAgent, its uv command adapter and BATI's machine-lane entry, offline. No Chrome, no Windows, no gateway.

    cd lancedb_memory && .venv/bin/python -m pytest -q agent_bridge_worker/tests/test_bati_desktop_agent.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.bati_desktop_process import UvBatiCommands
from agent_bridge_worker.bati_desktop_agent import BatiDesktopAgent
from agent_bridge_worker.cashy_agent import CashyCommandFailed, Machine
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_comms.tests.fakes import FakeClock

BATI     = Machine( hostname="desktop-shdbati", alias="BATI", agent="bati-desktop" )
MACHINES = Path( __file__ ).resolve().parents[ 1 ] / "machines.json"


def task( prompt: dict | str, operation: str = "implement", sender: str = "chatgpt-bridge", agent: str = "bati-desktop" ) -> ClaimedTask:
    mode = "read_only" if operation == "inspect" else "workspace_write"
    return ClaimedTask( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1, agent=agent,
                        workspace="bati-desktop", operation=operation, approval_mode=mode, sender=sender,
                        prompt=prompt if isinstance( prompt, str ) else json.dumps( prompt ))


class FakeRunning:
    def __init__( self, answer, error=None ):
        self.answer, self.error, self.stopped = answer, error, False

    def poll( self ):
        if self.error:
            raise CashyCommandFailed( self.error )
        return self.answer

    def stop( self ):
        self.stopped = True


class FakeCommands:
    def __init__( self, ok=True, error=None ):
        self.calls, self.ok, self.error = [], ok, error

    def start( self, operation, flags ):
        self.calls.append(( operation, dict( flags )))
        return FakeRunning({ "adw_id": "abc12345", "operation": operation, "ok": self.ok, "verified": self.ok,
                             "cause": "" if self.ok else "hidden_session", "message": f"{ operation } done on BATI",
                             "data": { "page": { "heading": "Plan for Today — November 21, 2026" }}}, self.error )


def agent( commands=None ):
    return BatiDesktopAgent( commands or FakeCommands(), FakeClock(), BATI, poll_seconds=0 )


def run( a, t ):
    return a.run( t, Path( "/tmp" ), lambda: "continue" )


def test_plan_for_today_runs_the_adw_and_reports_machine_and_adw_id():
    cmds = FakeCommands()
    out  = run( agent( cmds ), task({ "bati": "plan_for_today", "machine": "BATI" }))
    assert out.kind == OutcomeKind.SUCCEEDED and cmds.calls == [( "plan_for_today", {})]
    assert out.details[ "machine" ][ "hostname" ] == "desktop-shdbati" and out.details[ "adw_id" ] == "abc12345"
    assert "November 21, 2026" in out.body


def test_reads_are_inspect_and_steps_are_implement():
    assert run( agent(), task({ "bati": "status", "machine": "BATI" }, "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert run( agent(), task({ "bati": "status", "machine": "BATI" }, "implement" )).kind == OutcomeKind.REFUSED
    assert run( agent(), task({ "bati": "open", "machine": "BATI" }, "inspect" )).kind == OutcomeKind.REFUSED


def test_another_machine_is_refused_and_nothing_runs():
    cmds = FakeCommands()
    for name in ( "SQMC", "R46", "desktop-2obsqmc", "" ):
        out = run( agent( cmds ), task({ "bati": "status", "machine": name }, "inspect" ))
        assert out.kind == OutcomeKind.REFUSED
    assert run( agent( cmds ), task({ "bati": "status" }, "inspect" )).kind == OutcomeKind.REFUSED
    assert cmds.calls == []


def test_the_hostname_names_the_machine_too():
    assert run( agent(), task({ "bati": "status", "machine": "DESKTOP-SHDBATI" }, "inspect" )).kind == OutcomeKind.SUCCEEDED


def test_unknown_operations_fields_and_misplaced_values_are_refused():
    cmds = FakeCommands()
    for prompt, op in (({ "bati": "close", "machine": "BATI" }, "implement" ), ({ "bati": "type", "machine": "BATI" }, "implement" ),
                       ({ "bati": "status", "machine": "BATI", "text": "x" }, "inspect" ),
                       ({ "bati": "status", "machine": "BATI", "url": "http://localhost:8765/" }, "inspect" ),
                       ({ "bati": "open", "machine": "BATI", "url": "--help" }, "implement" ),
                       ({ "bati": "open", "machine": "BATI", "view": "plan-for-today" }, "implement" ), ( "not json", "inspect" )):
        assert run( agent( cmds ), task( prompt, op )).kind == OutcomeKind.REFUSED, prompt
    assert cmds.calls == []


def test_url_and_view_pass_through_for_the_adw_to_allowlist():
    cmds = FakeCommands()
    run( agent( cmds ), task({ "bati": "open", "machine": "BATI", "url": "http://localhost:8765/" }))
    run( agent( cmds ), task({ "bati": "read", "machine": "BATI", "view": "plan-for-today" }, "inspect" ))
    assert cmds.calls == [( "open", { "url": "http://localhost:8765/" }), ( "read", { "view": "plan-for-today" })]


def test_a_failed_or_silent_adw_is_a_failure_not_a_success():
    assert run( agent( FakeCommands( ok=False )), task({ "bati": "status", "machine": "BATI" }, "inspect" )).kind == OutcomeKind.FAILED
    out = run( agent( FakeCommands( error="exit status 1, no outcome file" )), task({ "bati": "status", "machine": "BATI" }, "inspect" ))
    assert out.kind == OutcomeKind.FAILED and "no outcome" in out.summary


def test_another_agent_name_is_refused():
    assert run( agent(), task({ "bati": "status", "machine": "BATI" }, "inspect", agent="cashy-r46" )).kind == OutcomeKind.REFUSED


def test_argv_is_fixed_and_values_are_flag_values():
    cmds = UvBatiCommands( Path( "/uv" ), Path( "/adws" ), Path( "/repo" ))
    argv = cmds.argv( "open", { "url": "http://localhost:8765/" }, "/tmp/r.json" )
    assert argv == [ "/uv", "run", "--quiet", "/adws/adw_bati_chrome.py", "open", "--url=http://localhost:8765/", "--json-out=/tmp/r.json" ]
    try:
        cmds.argv( "quit", {}, "/tmp/r.json" )
        raise AssertionError( "quit must not be an operation" )
    except CashyCommandFailed:
        pass


def test_bati_is_in_machines_json_beside_r46():
    assert Machine.load( MACHINES, "DESKTOP-SHDBATI" ) == BATI.model_copy( update={ "post_office_agents": ( "sheila", )})
    assert Machine.load( MACHINES, "rosemary46" ).agent == "cashy-r46"
    assert Machine.load( MACHINES, "desktop-2obsqmc" ) is None
