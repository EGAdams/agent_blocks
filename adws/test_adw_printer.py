"""Offline tests for adw_printer.py: the SSSF phases, the result line, and what each subcommand may do.

Every printer, spooler and clock is a fake from printer_agent/tests/fakes.py; nothing prints.

    cd adws && uv run --quiet --no-project --with pydantic --with pillow --with pyyaml --with rich \
        --with python-dotenv --with pytest pytest -q -p no:cacheprovider test_adw_printer.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE.parent / "lancedb_memory" / "printer_agent" / "tests" ))
sys.path.insert( 0, str( _HERE.parent / "lancedb_memory" ))
sys.path.insert( 0, str( _HERE ))

import adw_printer  # noqa: E402
from fakes import FakeBackend, Rig  # noqa: E402
from printer_agent.models import JobState, PrintLimits, PrintResult  # noqa: E402
from printer_agent.retention import sweep  # noqa: E402

JOB = "t_0123456789abcdef01234567"


@pytest.fixture( autouse=True )
def _session_files_in_tmp( tmp_path: Path, monkeypatch: pytest.MonkeyPatch ) -> None:
    monkeypatch.chdir( tmp_path )          # SSSF writes its session files under the working directory


def app_for( rig: Rig, tmp: Path, limits: PrintLimits = PrintLimits()) -> adw_printer.PrinterApplication:
    return adw_printer.PrinterApplication( workflow=rig.workflow, control=rig.control, limits=limits,
                                           spool_root=tmp / "spool" )


def call( argv: list[ str ], app: adw_printer.PrinterApplication, stdin: str = "" ) -> tuple[ int, dict, str ]:
    out = io.StringIO()
    with redirect_stdout( out ):
        code = adw_printer.main( argv, app=app, stdin=lambda: stdin )
    lines = out.getvalue().splitlines()
    assert lines and lines[ -1 ].startswith( adw_printer.MARKER ), "the last stdout line must be the result"
    return code, json.loads( lines[ -1 ][ len( adw_printer.MARKER ):]), out.getvalue()


def test_print_runs_the_seven_phases_and_reports_one_line( tmp_path: Path, capsys: pytest.CaptureFixture[ str ]) -> None:
    rig = Rig( tmp_path, backend=FakeBackend( PrintResult( state=JobState.COMPLETED_PER_SPOOLER, spooler_id=7 )))
    code, body, out = call([ "print", "--job-id", JOB ], app_for( rig, tmp_path ), '{"kind": "text", "text": "secret line"}' )
    assert code == 0 and body[ "job" ][ "state" ] == "completed_per_spooler"
    assert body[ "job" ][ "physical_output_verified" ] is False
    assert len( rig.backend.submitted ) == 1
    assert out.count( "\n" ) == 1, "only the result line goes to stdout"
    assert "secret line" not in out
    log = capsys.readouterr().err
    for phase in ( "receive_print_request", "validate_document", "resolve_printer", "render_document",
                   "submit_print_job", "monitor_print_job", "report_result" ):
        assert phase in log
    assert "secret line" not in log


def test_the_same_job_id_never_prints_twice( tmp_path: Path ) -> None:
    rig = Rig( tmp_path )
    app = app_for( rig, tmp_path )
    call([ "print", "--job-id", JOB ], app, '{"kind": "test_page"}' )
    code, body, _ = call([ "print", "--job-id", JOB ], app, '{"kind": "test_page"}' )
    assert len( rig.backend.submitted ) == 1
    assert body[ "job" ][ "job_id" ] == JOB


def test_a_bad_request_fails_before_anything_is_sent( tmp_path: Path ) -> None:
    rig = Rig( tmp_path )
    code, body, _ = call([ "print", "--job-id", JOB ], app_for( rig, tmp_path ), '{"kind": "text", "path": "/etc/passwd"}' )
    assert body[ "job" ][ "state" ] == "failed" and not rig.backend.submitted


def test_an_unknown_printer_is_refused_in_its_phase( tmp_path: Path ) -> None:
    rig = Rig( tmp_path )
    _, body, _ = call([ "print", "--job-id", JOB ], app_for( rig, tmp_path ),
                      '{"kind": "test_page", "printer_id": "no_such_printer"}' )
    assert body[ "job" ][ "state" ] == "failed" and "unknown printer" in body[ "job" ][ "detail" ]
    assert not rig.backend.submitted


def test_status_recent_and_discover_never_print( tmp_path: Path ) -> None:
    rig = Rig( tmp_path )
    app = app_for( rig, tmp_path )
    code, body, _ = call([ "status", "--job-id", JOB ], app )
    assert code == 1 and "error" in body                      # no such job: an error line, not a print
    for argv in ([ "recent" ], [ "discover" ]):
        code, body, _ = call( argv, app, '{"kind": "test_page"}' )
        assert code == 0 and "error" not in body
    assert not rig.backend.submitted
    assert body[ "default" ] is not None and body[ "printers" ]


def test_cancel_before_sending( tmp_path: Path ) -> None:
    rig = Rig( tmp_path )
    rig.workflow.receive( JOB, '{"kind": "test_page"}' )
    _, body, _ = call([ "cancel", "--job-id", JOB ], app_for( rig, tmp_path ))
    assert body[ "job" ][ "state" ] == "canceled" and not rig.backend.submitted


def test_a_bad_job_id_is_an_error( tmp_path: Path ) -> None:
    rig = Rig( tmp_path )
    for argv in ([ "print", "--job-id", "../x" ], [ "status" ], [ "cancel", "--job-id", "a b" ]):
        code, body, _ = call( argv, app_for( rig, tmp_path ), '{"kind": "test_page"}' )
        assert code == 1 and "job-id" in body[ "error" ]
    assert not rig.backend.submitted


def test_a_crash_is_an_error_line_without_a_traceback( tmp_path: Path ) -> None:
    rig = Rig( tmp_path )
    app = app_for( rig, tmp_path )
    rig.control.recent = lambda limit: 1 / 0                   # type: ignore[method-assign]
    code, body, out = call([ "recent" ], app )
    assert code == 1 and body == { "error": "the printer workflow failed (ZeroDivisionError)" }
    assert "Traceback" not in out


def test_sweep_removes_only_old_job_folders( tmp_path: Path ) -> None:
    root = tmp_path / "spool"
    for name in ( JOB, "t_ffffffffffffffffffffffff", "not a job" ):
        ( root / name ).mkdir( parents=True )
    old = time.time() - 3 * 86400
    os.utime( root / JOB, ( old, old ))
    os.utime( root / "not a job", ( old, old ))
    assert sweep( root, 86400.0, time.time()) == [ JOB ]
    assert sorted( p.name for p in root.iterdir()) == [ "not a job", "t_ffffffffffffffffffffffff" ]
    assert sweep( tmp_path / "missing", 1.0, time.time()) == []
