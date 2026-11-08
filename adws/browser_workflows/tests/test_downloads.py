"""Download setup must precede clicks and use the path native to Chrome."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.playback import download_directory as paths
from browser_workflows.playback.download_directory import DOWNLOAD_DIR_ENV, NativeDownloadDirectory
from browser_workflows.playback.downloads import FolderDownloadWatcher
from browser_workflows.playback.selenium_browser import SeleniumWorkflowBrowser


@pytest.fixture( autouse=True )
def clean_override( monkeypatch ):
    monkeypatch.delenv( DOWNLOAD_DIR_ENV, raising=False )


def test_wsl_download_path_is_a_windows_path_even_when_profile_contains_spaces( monkeypatch ):
    monkeypatch.setattr( paths.shutil, "which", lambda _: "/mnt/c/Windows/System32/cmd.exe" )
    monkeypatch.setattr( paths.subprocess, "run", lambda *a, **kw: SimpleNamespace( stdout="C:\\Users\\A Person\r\n" ))
    assert NativeDownloadDirectory().resolve() == r"C:\Users\A Person\Downloads\BrowserRecorder"


def test_missing_windows_profile_is_refused_instead_of_saving_to_a_linux_path( monkeypatch ):
    monkeypatch.setattr( paths.shutil, "which", lambda _: "cmd.exe" )
    monkeypatch.setattr( paths.subprocess, "run", lambda *a, **kw: SimpleNamespace( stdout="%USERPROFILE%\r\n" ))
    with pytest.raises( BrowserWorkflowError, match=DOWNLOAD_DIR_ENV ):
        NativeDownloadDirectory().resolve()


@pytest.mark.parametrize( "folder", [ r"D:\Statements", "/tmp/downloads" ])
def test_explicit_download_folder_is_preserved( folder, monkeypatch ):
    monkeypatch.setenv( DOWNLOAD_DIR_ENV, folder )
    assert NativeDownloadDirectory().resolve() == folder


def test_relative_override_is_refused( monkeypatch ):
    monkeypatch.setenv( DOWNLOAD_DIR_ENV, "downloads" )
    with pytest.raises( BrowserWorkflowError, match="absolute path" ):
        NativeDownloadDirectory().resolve()


def test_saved_folder_works_without_windows_interop_and_environment_takes_precedence( tmp_path, monkeypatch ):
    settings = tmp_path / "download_directory.txt"
    settings.write_text( "C:\\Users\\A Person\\Downloads\\BrowserRecorder\n" )
    monkeypatch.setattr( paths.shutil, "which", lambda _: None )
    assert NativeDownloadDirectory( settings ).resolve() == r"C:\Users\A Person\Downloads\BrowserRecorder"
    monkeypatch.setenv( DOWNLOAD_DIR_ENV, r"D:\Statements" )
    assert NativeDownloadDirectory( settings ).resolve() == r"D:\Statements"


def no_windows_side( monkeypatch ):
    """Neither PATH nor the known locations find cmd.exe -- a machine with no Windows behind it."""
    monkeypatch.setattr( paths.shutil, "which", lambda _: None )
    monkeypatch.setattr( paths, "CMD_EXE_PATHS", ())


def test_native_linux_default( monkeypatch ):
    no_windows_side( monkeypatch )
    monkeypatch.setattr( paths.os, "uname", lambda: SimpleNamespace( release="Linux" ))
    assert NativeDownloadDirectory().resolve() == str( Path.home() / "Downloads" / "BrowserRecorder" )


def test_wsl_without_interop_requires_an_explicit_windows_path( monkeypatch ):
    no_windows_side( monkeypatch )
    monkeypatch.setattr( paths.os, "uname", lambda: SimpleNamespace( release="microsoft-standard-WSL2" ))
    with pytest.raises( BrowserWorkflowError, match=DOWNLOAD_DIR_ENV ):
        NativeDownloadDirectory().resolve()


def test_cmd_exe_is_found_by_its_known_location_when_path_has_none( monkeypatch, tmp_path ):
    """A systemd user service inherits a plain PATH with none of WSL's Windows entries, so `which`
    finds nothing while interop itself works. That made a download from the Bridge worker fail with
    "cannot reach Windows from WSL" while the same download from a shell succeeded."""
    stand_in = tmp_path / "cmd.exe"
    stand_in.write_text( "" )
    monkeypatch.setattr( paths.shutil, "which", lambda _: None )
    monkeypatch.setattr( paths, "CMD_EXE_PATHS", ( str( tmp_path / "missing.exe" ), str( stand_in )))
    assert paths.windows_shell() == str( stand_in )
    monkeypatch.setattr( paths.subprocess, "run",
                         lambda *a, **k: SimpleNamespace( stdout=r"C:\Users\eg" + "\n" ))
    assert NativeDownloadDirectory().resolve() == r"C:\Users\eg\Downloads\BrowserRecorder"


def test_an_interop_socket_is_supplied_when_the_environment_has_none( monkeypatch ):
    """A systemd user service gets no WSL_INTEROP, and without one cmd.exe exits 1 -- which is why a
    download from the Bridge worker failed while the same download from a shell succeeded."""
    monkeypatch.delenv( paths.INTEROP_ENV, raising=False )
    monkeypatch.setattr( paths.Path, "exists", lambda self: str( self ) == paths.INIT_INTEROP )
    assert paths.interop_environment()[ paths.INTEROP_ENV ] == paths.INIT_INTEROP


def test_an_existing_interop_socket_is_left_alone( monkeypatch ):
    monkeypatch.setenv( paths.INTEROP_ENV, "/run/WSL/145381_interop" )
    assert paths.interop_environment()[ paths.INTEROP_ENV ] == "/run/WSL/145381_interop"


def test_a_machine_with_no_interop_socket_gets_no_variable_invented( monkeypatch ):
    monkeypatch.delenv( paths.INTEROP_ENV, raising=False )
    monkeypatch.setattr( paths.Path, "exists", lambda self: False )
    assert paths.INTEROP_ENV not in paths.interop_environment()


def test_path_wins_over_the_known_locations( monkeypatch ):
    monkeypatch.setattr( paths.shutil, "which", lambda _: "/usr/bin/cmd.exe" )
    assert paths.windows_shell() == "/usr/bin/cmd.exe"


def test_browser_reports_download_setup_errors():
    class Driver:
        def execute_cdp_cmd( self, cmd, args ):
            raise RuntimeError( "unsupported command" )

    browser = SeleniumWorkflowBrowser( SimpleNamespace( driver=Driver()), None )
    with pytest.raises( BrowserWorkflowError, match="cannot configure downloads" ):
        browser.configure_downloads( "/tmp/downloads" )


# ---- the folder watcher: what counts as an arrival -------------------------------------------------

def watcher( folder: Path ) -> FolderDownloadWatcher:
    return FolderDownloadWatcher( lambda: str( folder ), poll_seconds=0.01 )


def test_a_new_name_in_the_folder_is_an_arrival( tmp_path ):
    look  = watcher( tmp_path )
    before = look.before()
    ( tmp_path / "statement.pdf" ).write_bytes( b"%PDF-1.4 x" )
    assert [ p.name for p in look.wait_new( before, 2.0 )] == [ "statement.pdf" ]


def test_a_re_downloaded_file_of_the_same_name_is_an_arrival( tmp_path ):
    """Chrome overwrites rather than adding "(1)", so a name-only snapshot saw nothing arrive --
    which is how a real download through the Bridge was reported as "no file arrived"."""
    saved = tmp_path / "0337201.tif"
    saved.write_bytes( b"first" )
    look   = watcher( tmp_path )
    before = look.before()
    saved.write_bytes( b"second, longer" )
    assert [ p.name for p in look.wait_new( before, 2.0 )] == [ "0337201.tif" ]


def test_a_folder_that_does_not_change_reports_nothing_rather_than_a_file( tmp_path ):
    ( tmp_path / "old.pdf" ).write_bytes( b"%PDF-1.4" )
    look = watcher( tmp_path )
    assert look.wait_new( look.before(), 0.1 ) == []


def test_a_file_still_being_written_is_not_reported_until_its_partner_is_gone( tmp_path ):
    look   = watcher( tmp_path )
    before = look.before()
    ( tmp_path / "big.zip" ).write_bytes( b"half" )
    ( tmp_path / "big.zip.crdownload" ).write_bytes( b"rest" )
    assert look.wait_new( before, 0.1 ) == []


def test_a_file_whose_size_is_still_moving_is_not_reported_as_finished( tmp_path ):
    """Stamps must settle for one poll: an overwrite is written in place, so a file briefly exists
    short, and a truncated statement must not be handed back as the download."""
    growing = tmp_path / "grows.bin"
    look    = watcher( tmp_path )
    before  = look.before()
    growing.write_bytes( b"a" )
    real_stat, calls = Path.stat, { "n": 0 }

    def moving_stat( self, *args, **kwargs ):
        info = real_stat( self, *args, **kwargs )
        if self.name == "grows.bin":
            calls[ "n" ] += 1
            return SimpleNamespace( st_size=calls[ "n" ], st_mtime_ns=calls[ "n" ], st_mode=info.st_mode )
        return info

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr( Path, "stat", moving_stat )
        assert look.wait_new( before, 0.1 ) == []
