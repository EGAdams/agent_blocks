"""PrinterAgent, the printer policy, and the uv command adapter, offline. Nothing prints.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_printer_agent
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.printer_process import UvPrinterCommands
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.policy import FixedFirstWorkspaces, ScopedPolicy, StaticWorkspaces
from agent_bridge_worker.printer_agent import PrinterAgent, PrinterCommandFailed, describe
from agent_bridge_worker.service import BridgeTaskRun
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID, FakeGateway
from agent_comms.tests.fakes import FakeClock

OTHER   = "t_" + "b" * 24
REQUEST = json.dumps({ "kind": "text", "title": "Note", "text": "private words", "confirm": True })
CAPS    = { "printer": { "print": False, "print_cancel": False, "print_status": False, "print_discover": False }}
MODES   = frozenset({ "read_only", "workspace_write", "device_control" })


def task( operation: str = "print", prompt: str = REQUEST, mode: str = "device_control", **over ) -> ClaimedTask:
    fields = dict( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1,
                   agent="printer", workspace="printers", operation=operation, approval_mode=mode, prompt=prompt )
    fields.update( over )
    return ClaimedTask( **fields )


def job( state: str = "completed_per_spooler", **over ) -> dict:
    return { "job_id": TASK_ID, "state": state, "printer_id": "hpi297bea", "printer": "HPI297BEA (HP OfficeJet 8120e series)",
             "title": "Note", "pages": 1, "copies": 1, "detail": "", "physical_output_verified": False, **over }


class FakeRunning:
    def __init__( self, answer: dict | None, ticks: int = 0, error: str | None = None ) -> None:
        self.answer, self.ticks, self.error, self.stopped = answer, ticks, error, False

    def poll( self ) -> dict | None:
        if self.ticks > 0:
            self.ticks -= 1
            return None
        if self.error:
            raise PrinterCommandFailed( self.error )
        return self.answer

    def stop( self ) -> None:
        self.stopped = True


class FakeCommands:
    def __init__( self, answer: dict | None = None, ticks: int = 0, error: str | None = None ) -> None:
        self.started: list[ tuple ] = []
        self.running = FakeRunning( answer if answer is not None else job(), ticks, error )

    def start( self, subcommand: str, job_id: str | None, request: str | None ) -> FakeRunning:
        self.started.append(( subcommand, job_id, request ))
        return self.running


def go() -> str:
    return "continue"


def run( commands: FakeCommands, t: ClaimedTask, keep_alive=go, **kw ):
    return PrinterAgent( commands, FakeClock(), **kw ).run( t, Path( "/nonexistent" ), keep_alive )


# -- PrinterAgent ---------------------------------------------------------------------

def test_a_print_runs_once_keyed_by_the_task_and_passes_the_request_on_stdin() -> None:
    c   = FakeCommands()
    out = run( c, task())
    assert c.started == [( "print", TASK_ID, REQUEST )]
    assert out.kind is OutcomeKind.SUCCEEDED and out.retryable is False
    assert out.details[ "job" ][ "state" ] == "completed_per_spooler"
    assert "paper output is not verified" in out.summary
    assert "private words" not in out.summary + out.body          # document text never comes back


def test_submitted_but_not_finished_is_still_a_success_that_says_so() -> None:
    out = run( FakeCommands( job( "submitted_to_spooler" )), task())
    assert out.kind is OutcomeKind.SUCCEEDED and "has not left the queue" in out.summary


def test_failed_and_ambiguous_jobs_are_failures_that_are_never_retried() -> None:
    for state, words in (( "failed", "not printed" ), ( "ambiguous", "will not be reprinted" )):
        out = run( FakeCommands( job( state, detail="the spooler refused the job" )), task())
        assert out.kind is OutcomeKind.FAILED and out.retryable is False, state
        assert words in out.summary and out.details[ "job" ][ "state" ] == state


def test_a_crashed_workflow_is_a_failure_not_a_success() -> None:
    out = run( FakeCommands( error="exit status 1, no result line" ), task())
    assert out.kind is OutcomeKind.FAILED and out.retryable is False and "no answer" in out.summary


def test_an_error_answer_is_a_failure() -> None:
    out = run( FakeCommands({ "error": "unknown printer x" }), task( "print_status", f"job={ OTHER }", "read_only" ))
    assert out.kind is OutcomeKind.FAILED and "unknown printer" in out.summary


def test_a_print_is_never_stopped_by_a_cancel_or_a_lost_lease() -> None:
    for signal in ( "cancel", "lost" ):
        c   = FakeCommands( ticks=3 )
        out = run( c, task(), keep_alive=lambda s=signal: s )
        assert c.running.stopped is False, signal              # it ran to its end
        if signal == "lost":
            assert out.kind is OutcomeKind.LEASE_LOST
        else:
            assert out.kind is OutcomeKind.SUCCEEDED and "printer_cancel" in out.summary


def test_status_checks_never_print_and_can_be_stopped() -> None:
    c   = FakeCommands( job( "submitted_to_spooler" ))
    out = run( c, task( "print_status", f"job={ OTHER }", "read_only" ))
    assert c.started == [( "status", OTHER, None )] and out.kind is OutcomeKind.SUCCEEDED
    c   = FakeCommands({ "jobs": [ job()]})
    out = run( c, task( "print_status", "recent", "read_only" ))
    assert c.started == [( "recent", None, None )] and out.details[ "jobs" ][ 0 ][ "job_id" ] == TASK_ID
    c   = FakeCommands( ticks=5 )
    out = run( c, task( "print_status", "recent", "read_only" ), keep_alive=lambda: "cancel" )
    assert c.running.stopped and out.kind is OutcomeKind.CANCELLED


def test_cancel_names_its_job_and_fails_unless_it_was_canceled() -> None:
    c   = FakeCommands( job( "canceled", job_id=OTHER ))
    out = run( c, task( "print_cancel", f"cancel={ OTHER }" ))
    assert c.started == [( "cancel", OTHER, None )] and out.kind is OutcomeKind.SUCCEEDED
    out = run( FakeCommands( job( "completed_per_spooler", job_id=OTHER )), task( "print_cancel", f"cancel={ OTHER }" ))
    assert out.kind is OutcomeKind.FAILED


def test_discover_lists_printers_and_the_default() -> None:
    answer = { "printers": [{ "printer_id": "hpi297bea", "is_default": True }, { "printer_id": "hp063e28", "is_default": False }],
               "default": "hpi297bea", "documents": [], "recent": []}
    c   = FakeCommands( answer )
    out = run( c, task( "print_discover", "discover", "read_only" ))
    assert c.started == [( "discover", None, None )]
    assert out.kind is OutcomeKind.SUCCEEDED and "hpi297bea (default)" in out.summary
    assert out.details[ "default" ] == "hpi297bea"


def test_prompts_other_than_the_fixed_shapes_are_refused_and_nothing_starts() -> None:
    bad = [( "print_cancel", "cancel=t_x; rm -rf /" ), ( "print_cancel", f"cancel={ OTHER } please" ),
           ( "print_status", "job=../../etc/passwd" ), ( "print_discover", "discover; echo" ), ( "scan", "scanner=window" )]
    for operation, prompt in bad:
        c   = FakeCommands()
        out = run( c, task( operation, prompt ))
        assert out.kind is OutcomeKind.REFUSED and c.started == [], ( operation, prompt )


def test_describe_never_claims_paper() -> None:
    for state in ( "submitted_to_spooler", "completed_per_spooler", "failed", "ambiguous", "canceled" ):
        line = describe( job( state )).lower()
        assert "printed successfully" not in line and "paper came out" not in line


# -- policy -------------------------------------------------------------------------------

def test_printing_runs_only_in_device_control_and_status_only_read_only() -> None:
    policy = ScopedPolicy( CAPS, frozenset(), MODES )
    assert policy.refusal( task()) is None
    assert policy.refusal( task( "print_cancel", f"cancel={ OTHER }" )) is None
    assert policy.refusal( task( "print_status", "recent", "read_only" )) is None
    assert policy.refusal( task( "print_discover", "discover", "read_only" )) is None
    assert "device_control" in policy.refusal( task( mode="read_only" ))
    assert "read_only" in policy.refusal( task( "print_status", "recent", "device_control" ))
    assert policy.refusal( task( agent="codex" )) is not None
    assert "not enabled" in ScopedPolicy( CAPS, frozenset()).refusal( task())   # printing off by default


# -- the uv command adapter -----------------------------------------------------------

def test_argv_is_fixed_apart_from_a_checked_job_id() -> None:
    cmds = UvPrinterCommands( Path( "/uv" ), Path( "/adws/adw_printer.py" ), Path( "/" ))
    assert cmds.argv( "print", TASK_ID ) == [ "/uv", "run", "--quiet", "/adws/adw_printer.py", "print", "--job-id", TASK_ID ]
    assert cmds.argv( "discover", None ) == [ "/uv", "run", "--quiet", "/adws/adw_printer.py", "discover" ]
    for sub, jid in (( "rm", None ), ( "status", "t_1; ls" ), ( "status", "--help" )):
        try:
            cmds.argv( sub, jid )
            raise AssertionError(( sub, jid ))
        except PrinterCommandFailed:
            pass


def test_the_adapter_reads_only_the_result_line() -> None:
    work = Path( tempfile.mkdtemp())
    fake = work / "fake_uv.sh"
    fake.write_text( '#!/bin/sh\nread -r line\necho "phase noise $5"\necho \'PRINTER_RESULT {"state": "canceled", "got": 1}\'\n' )
    fake.chmod( 0o700 )
    running = UvPrinterCommands( fake, work / "adw.py", work ).start( "cancel", TASK_ID, "x\n" )
    clock = FakeClock()
    import time
    for _ in range( 200 ):
        answer = running.poll()
        if answer is not None:
            break
        time.sleep( 0.02 )
    assert answer == { "state": "canceled", "got": 1 }
    silent = work / "silent.sh"
    silent.write_text( "#!/bin/sh\necho no result\nexit 3\n" )
    silent.chmod( 0o700 )
    running = UvPrinterCommands( silent, work / "adw.py", work ).start( "discover", None, None )
    try:
        for _ in range( 200 ):
            if running.poll() is not None:
                break
            time.sleep( 0.02 )
        raise AssertionError( "no result line should fail" )
    except PrinterCommandFailed as exc:
        assert "exit status 3" in str( exc )
    del clock


# -- bridge -> worker -> agent round trip ---------------------------------------------------

def test_round_trip_through_the_bridge_worker() -> None:
    gateway = FakeGateway( operation="print", agent="printer", mode="device_control", workspace="printers" )
    gateway._claim[ "prompt" ] = REQUEST
    commands = FakeCommands( ticks=2 )
    clock    = FakeClock()
    agent    = PrinterAgent( commands, clock )
    work     = Path( tempfile.mkdtemp())
    resolver = FixedFirstWorkspaces({ "printers": work }, StaticWorkspaces({}))
    svc      = BridgeTaskRun( gateway, ScopedPolicy( CAPS, frozenset(), MODES ), resolver, { "printer": agent }, agent,
                              clock, "worker-test" )
    report = svc.run_one()
    assert report.outcome is OutcomeKind.SUCCEEDED, report
    assert commands.started == [( "print", TASK_ID, REQUEST )]
    assert gateway.completed[ "ok" ] is True and gateway.completed[ "performer" ] == "printer"
    assert gateway.completed[ "details" ][ "job" ][ "state" ] == "completed_per_spooler"
    assert gateway.beats >= 1                                              # it heartbeated while waiting


def test_round_trip_failure_is_completed_with_details_and_no_file_talk() -> None:
    gateway = FakeGateway( operation="print", agent="printer", mode="device_control", workspace="printers" )
    gateway._claim[ "prompt" ] = REQUEST
    agent    = PrinterAgent( FakeCommands( job( "failed", detail="unknown printer zz" )), FakeClock())
    resolver = FixedFirstWorkspaces({ "printers": Path( tempfile.mkdtemp())}, StaticWorkspaces({}))
    BridgeTaskRun( gateway, ScopedPolicy( CAPS, frozenset(), MODES ), resolver, { "printer": agent }, agent,
                   FakeClock(), "worker-test" ).run_one()
    assert gateway.completed[ "ok" ] is False and gateway.failed is None
    assert gateway.completed[ "details" ][ "job" ][ "state" ] == "failed"
    assert "file" not in gateway.completed[ "summary" ]                    # not a write task's wording


def test_reads_the_adw_wrapped_answers():
    # adw_printer.py prints {"job": {...}} and {"recent": [...]}; a flat fake once hid that.
    done = job( state="completed_per_spooler" )
    out  = run( FakeCommands({ "job": done }), task())
    assert out.kind == OutcomeKind.SUCCEEDED and TASK_ID in out.summary and out.details[ "job" ][ "state" ] == "completed_per_spooler"
    out  = run( FakeCommands({ "recent": [ done ]}), task( "print_status", "recent", "read_only" ))
    assert out.details[ "jobs" ][ 0 ][ "job_id" ] == TASK_ID


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )
