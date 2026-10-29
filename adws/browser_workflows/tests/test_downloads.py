"""Download setup must precede clicks and use the path native to Chrome."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.playback import download_directory as paths
from browser_workflows.playback.download_directory import DOWNLOAD_DIR_ENV, NativeDownloadDirectory
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


def test_native_linux_default( monkeypatch ):
    monkeypatch.setattr( paths.shutil, "which", lambda _: None )
    monkeypatch.setattr( paths.os, "uname", lambda: SimpleNamespace( release="Linux" ))
    assert NativeDownloadDirectory().resolve() == str( Path.home() / "Downloads" / "BrowserRecorder" )


def test_wsl_without_interop_requires_an_explicit_windows_path( monkeypatch ):
    monkeypatch.setattr( paths.shutil, "which", lambda _: None )
    monkeypatch.setattr( paths.os, "uname", lambda: SimpleNamespace( release="microsoft-standard-WSL2" ))
    with pytest.raises( BrowserWorkflowError, match=DOWNLOAD_DIR_ENV ):
        NativeDownloadDirectory().resolve()


def test_browser_reports_download_setup_errors():
    class Driver:
        def execute_cdp_cmd( self, cmd, args ):
            raise RuntimeError( "unsupported command" )

    browser = SeleniumWorkflowBrowser( SimpleNamespace( driver=Driver()), None )
    with pytest.raises( BrowserWorkflowError, match="cannot configure downloads" ):
        browser.configure_downloads( "/tmp/downloads" )
