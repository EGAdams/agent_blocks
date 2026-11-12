"""CashyMachineAgent, its uv command adapter and the machine lane, offline. No Chrome, no bank, no gateway.

    cd lancedb_memory
    uv run --quiet --with pydantic --with pytest pytest -q agent_bridge_worker/tests/test_cashy_agent.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.cashy_process import UvCashyCommands
from agent_bridge_worker.adapters.http_gateway import HttpTaskGateway
from agent_bridge_worker.cashy_agent import CashyCommandFailed, CashyMachineAgent, Machine, mask
from agent_bridge_worker.lanes import WorkerLane
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.ports import GatewayError
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_comms.tests.fakes import FakeClock

R46 = Machine( hostname="rosemary46", alias="R46", agent="cashy-r46" )


def task( prompt: dict | str, operation: str = "implement", sender: str = "chatgpt-bridge", agent: str = "cashy-r46" ) -> ClaimedTask:
    mode = "read_only" if operation == "inspect" else "workspace_write"
    return ClaimedTask( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1, agent=agent,
                        workspace="cashy-r46", operation=operation, approval_mode=mode, sender=sender,
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
    """Keeps Cashy's session the way her SQLite store would: per session id, across tasks."""

    def __init__( self, error=None ):
        self.calls, self.error, self.sessions = [], error, {}

    def start( self, adw, flags ):
        self.calls.append(( adw, dict( flags )))
        s = self.sessions.setdefault( flags[ "session" ], { "id": flags[ "session" ], "state": "idle", "bank": "", "target_id": "", "url": "" })
        if adw == "open_bank":
            s.update( state="awaiting_user_login", bank="fifth_third", target_id="TAB1",
                      url="https://www.53.com/content/fifth-third/en/login.html" )
        line = "The Fifth Third login page is ready." if adw == "open_bank" else f"Cashy is { s[ 'state' ]}."
        return FakeRunning({ "ok": True, "line": line, "data": { "state": s[ "state" ], "events": [], "session": dict( s )}}, self.error )


class Titles:
    def title( self, target_id ):
        return "Fifth Third Bank | Login" if target_id == "TAB1" else ""


def agent( commands=None ):
    return CashyMachineAgent( commands or FakeCommands(), FakeClock(), R46, Titles(), poll_seconds=0 )


def run( a, t ):
    return a.run( t, Path( "/tmp" ), lambda: "continue" )


def test_open_bank_on_r46_reports_machine_session_tab_and_url():
    cmds = FakeCommands()
    out  = run( agent( cmds ), task({ "cashy": "open_bank", "machine": "R46", "session": "r46-bridge", "bank": "fifth_third" }))
    assert out.kind == OutcomeKind.SUCCEEDED, out.summary
    assert cmds.calls == [( "open_bank", { "session": "r46-bridge", "actor": "chatgpt-bridge", "bank": "fifth_third", "launch_chrome": "true" })]
    d = out.details
    assert d[ "machine" ] == { "hostname": "rosemary46", "alias": "R46", "agent": "cashy-r46" }
    assert ( d[ "session" ], d[ "state" ], d[ "tab" ], d[ "title" ]) == ( "r46-bridge", "awaiting_user_login", "TAB1", "Fifth Third Bank | Login" )
    assert d[ "url" ].startswith( "https://www.53.com/" )
    assert "R46 rosemary46 session r46-bridge: awaiting_user_login" in out.summary


def test_the_hostname_names_the_machine_too_and_case_does_not_matter():
    out = run( agent(), task({ "cashy": "status", "machine": "Rosemary46", "session": "s1" }, "inspect" ))
    assert out.kind == OutcomeKind.SUCCEEDED, out.summary


def test_another_machine_is_refused_with_both_names_and_nothing_runs():
    cmds = FakeCommands()
    out  = run( agent( cmds ), task({ "cashy": "open_bank", "machine": "SQMC", "session": "s1", "bank": "fifth_third" }))
    assert out.kind == OutcomeKind.REFUSED
    assert "'SQMC'" in out.summary and "R46 (rosemary46)" in out.summary and "nothing was opened" in out.summary
    assert cmds.calls == []


def test_machine_and_session_are_required_every_time():
    cmds = FakeCommands()
    for prompt in ({ "cashy": "status", "session": "s1" }, { "cashy": "status", "machine": "R46" },
                   { "cashy": "status", "machine": "R46", "session": "../etc" }, { "cashy": "status", "machine": "R46", "session": "-x" }):
        assert run( agent( cmds ), task( prompt, "inspect" )).kind == OutcomeKind.REFUSED, prompt
    assert cmds.calls == []


def test_the_session_continues_across_tasks():
    cmds = FakeCommands()
    a    = agent( cmds )
    run( a, task({ "cashy": "open_bank", "machine": "R46", "session": "r46-bridge", "bank": "fifth_third" }))
    out  = run( a, task({ "cashy": "status", "machine": "R46", "session": "r46-bridge" }, "inspect" ))
    assert out.details[ "state" ] == "awaiting_user_login" and out.details[ "tab" ] == "TAB1"
    other = run( a, task({ "cashy": "status", "machine": "R46", "session": "another" }, "inspect" ))
    assert other.details[ "state" ] == "idle"


def test_the_actor_is_the_bridge_sender_never_user():
    cmds = FakeCommands()
    run( agent( cmds ), task({ "cashy": "logged_in", "machine": "R46", "session": "s1" }, sender="toyota-bridge" ))
    assert cmds.calls[ 0 ][ 1 ][ "actor" ] == "toyota-bridge"
    for _, flags in cmds.calls:
        assert flags[ "actor" ] != "user"


def test_a_request_cannot_set_the_actor_or_any_other_flag():
    cmds = FakeCommands()
    out  = run( agent( cmds ), task({ "cashy": "logged_in", "machine": "R46", "session": "s1", "actor": "user" }))
    assert out.kind == OutcomeKind.REFUSED and "actor" in out.summary and cmds.calls == []


def test_reads_and_writes_match_inspect_and_implement():
    assert run( agent(), task({ "cashy": "open_bank", "machine": "R46", "session": "s", "bank": "fifth_third" }, "inspect" )).kind == OutcomeKind.REFUSED
    assert run( agent(), task({ "cashy": "status", "machine": "R46", "session": "s" }, "implement" )).kind == OutcomeKind.REFUSED
    assert run( agent(), task({ "cashy": "transfer", "machine": "R46", "session": "s" })).kind == OutcomeKind.REFUSED
    assert run( agent(), task({ "cashy": "pause", "machine": "R46", "session": "s" })).kind == OutcomeKind.SUCCEEDED


def test_list_options_runs_the_read_page_adw():
    cmds = FakeCommands()
    run( agent( cmds ), task({ "cashy": "list_options", "machine": "R46", "session": "s" }, "inspect" ))
    assert cmds.calls[ 0 ][ 0 ] == "read_page"


def test_another_agent_name_is_refused():
    out = run( agent(), task({ "cashy": "status", "machine": "R46", "session": "s" }, "inspect", agent="cashy" ))
    assert out.kind == OutcomeKind.REFUSED


def test_an_unavailable_cashy_is_a_failure_naming_the_machine():
    out = run( agent( FakeCommands( error="exit status 1, no outcome file" )), task({ "cashy": "status", "machine": "R46", "session": "s" }, "inspect" ))
    assert out.kind == OutcomeKind.FAILED and "R46" in out.summary and out.details[ "session" ] == "s"


def test_titles_mask_long_digit_runs():
    assert mask( "Checking 1234567890" ) == "Checking ******7890"


def test_uv_argv_passes_values_as_values_and_bool_as_a_flag():
    argv = UvCashyCommands( Path( "/uv" ), Path( "/adws" ), Path( "/" )).argv(
        "open_bank", { "session": "s", "actor": "chatgpt-bridge", "bank": "fifth_third", "launch_chrome": "true" }, "/tmp/r" )
    assert argv[ :4 ] == [ "/uv", "run", "--quiet", "/adws/adw_cashy_open_bank.py" ]
    assert "--launch-chrome" in argv and "--bank=fifth_third" in argv and "--actor=chatgpt-bridge" in argv
    assert argv[ -2: ] == [ "--port=9222", "--json-out=/tmp/r" ]
    try:
        UvCashyCommands( Path( "/uv" ), Path( "/adws" ), Path( "/" )).argv( "transfer", {}, "/r" )
        raise AssertionError( "an unknown ADW must not run" )
    except CashyCommandFailed:
        pass


def test_machines_file_names_r46(tmp_path=None):
    path = Path( __file__ ).resolve().parents[ 1 ] / "machines.json"
    assert Machine.load( path, "Rosemary46" ) == R46
    assert Machine.load( path, "desktop-2obsqmc" ) is None


def test_machine_lane_takes_only_its_pins():
    lane = WorkerLane( name="machine" )
    assert lane.pinned and lane.skips([ "agent_blocks" ]) == []
    assert lane.unit == "agent-bridge-worker-machine.service"
    assert lane.default_worker_id( "rosemary46" ) == "wsl-rosemary46-machine"
    assert lane.poll_state_path.name == "poll_state_machine.json"
    assert not WorkerLane( name="express" ).pinned


class Recording( HttpTaskGateway ):
    def __init__( self, *a, error=None, **kw ):
        super().__init__( *a, **kw )
        self.paths, self.error = [], error

    def _call( self, method, path, body=None ):
        self.paths.append( path )
        if self.error:
            raise self.error
        return 204, None


def test_gateway_asks_for_pinned_and_probes_for_pin_support():
    g = Recording( "https://bridge.example/v1", "t", pinned=True )
    assert g.next() is None and g.paths == [ "/worker/tasks/next?pinned=1" ]
    assert Recording( "https://bridge.example/v1", "t", error=GatewayError( 400, "bad_pinned", "x" )).supports_pins()
    assert not Recording( "https://bridge.example/v1", "t" ).supports_pins()     # an old gateway ignores it


if __name__ == "__main__":
    import pytest
    sys.exit( pytest.main([ "-q", __file__ ]))
