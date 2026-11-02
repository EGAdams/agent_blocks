"""The Windows adapter with PowerShell mocked: parsing, hiding virtual queues, and what goes on the command line."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from fakes import OFFICEJET, FakeRunner
from printer_agent.adapters.windows_spooler import ( DISCOVER, PowerShell, WindowsPrinterDiscovery, WindowsSpoolerBackend,
                                                    interpret, printer_id_for, windows_path )
from printer_agent.errors import SpoolerError, SpoolerRefused
from printer_agent.models import CommandOutput, JobState, PrintJob, PrintOptions, RenderedDocument, RenderedPage

LISTING = { "printers": [
    { "name": "HPI297BEA (HP OfficeJet 8120e series)", "driver": "HP OfficeJet 8120e series PCL-3", "port": "WSD-1",
      "shared": False, "status": "Normal", "is_default": True },
    { "name": "HP063E28 (HP DeskJet 4100 series)", "driver": "HP DeskJet 4100", "port": "WSD-2", "shared": False,
      "status": "Normal", "is_default": False },
    { "name": "Microsoft Print to PDF", "port": "PORTPROMPT:", "is_default": False },
    { "name": "Microsoft XPS Document Writer", "port": "PORTPROMPT:", "is_default": False },
    { "name": "OneNote (Desktop)", "port": "nul:", "is_default": False },
    { "name": "Fax", "port": "SHRFAX:", "is_default": False },
]}


def answering( body: dict, code: int = 0 ) -> FakeRunner:
    return FakeRunner({ "powershell.exe": CommandOutput( returncode=code, stdout=json.dumps( body ).encode())})


def script_of( argv: list[ str ]) -> str:
    return base64.b64decode( argv[ argv.index( "-EncodedCommand" ) + 1 ]).decode( "utf-16-le" )


def test_discovery_hides_virtual_queues_and_marks_the_default():
    runner = answering( LISTING )
    found = WindowsPrinterDiscovery( PowerShell( runner )).list_printers()
    assert [ p.printer_id for p in found ] == [ "hpi297bea_hp_officejet_8120e_series", "hp063e28_hp_deskjet_4100_series" ]
    assert found[ 0 ].is_default and not found[ 1 ].is_default


def test_discovery_only_reads():
    runner = answering( LISTING )
    WindowsPrinterDiscovery( PowerShell( runner )).list_printers()
    script = script_of( runner.calls[ 0 ][ 0 ])
    assert script == DISCOVER
    for verb in ( ".Print(", "Set-Printer", "Add-Printer", "Add-PrinterDriver", "Remove-", "rundll32" ):
        assert verb not in script


def test_request_content_never_reaches_the_command_line( tmp_path ):
    spool = Path( "/mnt/c/fake" )
    runner = answering({ "spooler_id": 12, "flags": "Spooling" })
    backend = WindowsSpoolerBackend( PowerShell( runner ), tmp_path / "spool" )
    job = PrintJob( job_id="t_cmdline001", printer=OFFICEJET, title="secret title; & del *",
                    rendered=RenderedDocument( pages=[ RenderedPage( png=b"\x89PNG" )]), options=PrintOptions())
    with pytest.raises( SpoolerRefused ):          # tmp_path is not on a Windows drive
        backend.submit( job )
    assert runner.calls == []
    assert not ( tmp_path / "spool" / "t_cmdline001" ).exists()


def test_submit_sends_a_json_spec_on_stdin( tmp_path, monkeypatch ):
    import printer_agent.adapters.windows_spooler as ws
    monkeypatch.setattr( ws, "windows_path", lambda p: "C:\\spool\\" + p.name )
    runner = answering({ "spooler_id": 12, "flags": "Spooling" })
    backend = WindowsSpoolerBackend( PowerShell( runner ), tmp_path / "spool" )
    job = PrintJob( job_id="t_submit0001", printer=OFFICEJET, title="secret & del *",
                    rendered=RenderedDocument( pages=[ RenderedPage( png=b"\x89PNG" )] * 2 ),
                    options=PrintOptions( copies=2 ))
    result = backend.submit( job )
    argv, stdin = runner.calls[ 0 ]
    assert "secret" not in " ".join( argv )
    spec = json.loads( stdin )
    assert spec == { "printer": OFFICEJET.name, "document_name": "STB t_submit0001", "copies": 2, "landscape": False,
                     "pages": [ "C:\\spool\\page_001.png", "C:\\spool\\page_002.png" ]}
    assert result.state is JobState.SUBMITTED_TO_SPOOLER and result.spooler_id == 12
    assert not ( tmp_path / "spool" / "t_submit0001" ).exists()          # page images removed


def test_powershell_errors():
    with pytest.raises( SpoolerRefused ):
        PowerShell( answering({ "error": "printer_not_found" }, code=3 )).call( "x", {}, 1.0 )
    with pytest.raises( SpoolerError ):
        PowerShell( FakeRunner({ "powershell.exe": CommandOutput( returncode=124 )})).call( "x", {}, 1.0 )
    with pytest.raises( SpoolerError ):
        PowerShell( FakeRunner({ "powershell.exe": CommandOutput( returncode=1, stdout=b"boom" )})).call( "x", {}, 1.0 )


def test_powershell_error_keeps_one_clipped_stderr_line():
    err = b"\r\nException calling \"Print\" with \"0\" argument(s): \"The RPC server is unavailable\"\r\n" + b"x" * 500
    with pytest.raises( SpoolerError ) as caught:
        PowerShell( FakeRunner({ "powershell.exe": CommandOutput( returncode=1, stderr=err )})).call( "x", {}, 1.0 )
    message = str( caught.value )
    assert message.startswith( "powershell_exit_1: Exception calling" ) and "RPC server" in message and len( message ) < 200


def test_interpret_never_claims_paper():
    state, detail = interpret( "", in_queue=False )
    assert state is JobState.COMPLETED_PER_SPOOLER and "not verified" in detail
    assert interpret( "Printing", True )[ 0 ] is JobState.SUBMITTED_TO_SPOOLER
    state, detail = interpret( "Error, PaperOut", True )
    assert state is JobState.SUBMITTED_TO_SPOOLER and "PaperOut" in detail
    assert interpret( "Deleting", True )[ 0 ] is JobState.CANCELED


def test_cancel_and_status_parse():
    backend = WindowsSpoolerBackend( PowerShell( answering({ "removed": 1 })), Path( "/nonexistent" ))
    assert backend.cancel( "t_x00000001", OFFICEJET ).state is JobState.CANCELED
    backend = WindowsSpoolerBackend( PowerShell( answering({ "removed": 0 })), Path( "/nonexistent" ))
    assert backend.cancel( "t_x00000001", OFFICEJET ).state is JobState.FAILED
    backend = WindowsSpoolerBackend( PowerShell( answering({ "in_queue": True, "flags": "Printing", "pages": 3,
                                                             "printer": "Normal" })), Path( "/nonexistent" ))
    status = backend.status( "t_x00000001", OFFICEJET )
    assert status.in_queue and status.pages == 3 and status.flags == [ "Printing" ]


def test_ids_and_paths():
    assert printer_id_for( "HPI297BEA (HP OfficeJet 8120e series)" ) == "hpi297bea_hp_officejet_8120e_series"
    assert windows_path( Path( "/mnt/c/Users/NewUser/x.png" )) == "C:\\Users\\NewUser\\x.png"
    with pytest.raises( SpoolerRefused ):
        windows_path( Path( "/home/adamsl/x.png" ))
