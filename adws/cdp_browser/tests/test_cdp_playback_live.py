"""Live: an ordinary saved playback, run unchanged over the CDP transport."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from browser_workflows.models import LocatorCandidate, RecordedAction, WaitCondition

from .live_site import PAGES, site  # noqa: F401  (a fixture)

PORT = os.environ.get( "CDP_TEST_PORT" )
pytestmark = pytest.mark.skipif( not PORT, reason="set CDP_TEST_PORT to a local debug Chrome" )


def test_saved_playback_runs_over_cdp( site: str, tmp_path: Path, monkeypatch ) -> None:
    from browser_workflows.kit import WorkflowKit
    from browser_workflows.operations.catalog import operations
    from browser_workflows.tests.fakes import ScriptedConsole

    downloads = tmp_path / "downloads"
    monkeypatch.setenv( "BROWSER_WORKFLOW_DOWNLOAD_DIR", str( downloads ))
    kit = WorkflowKit( db_path=tmp_path / "db.sqlite", log_path=tmp_path / "logs" / "r.jsonl", transport="cdp" )
    kit.repository().create( "Test Bank statement", [
        RecordedAction( action_type="navigate", url=site + "/" ),
        RecordedAction( action_type="click", url=site + "/",
                        locators=[ LocatorCandidate( strategy="text", value="Statements", tag="a", source="text" )],
                        wait_after=[ WaitCondition( kind="url_contains", value="/statements" )]),
        RecordedAction( action_type="click", url=site + "/statements",
                        locators=[ LocatorCandidate( strategy="css", value="#dl", tag="a", source="id" )]),
    ], "two clicks and a download" )

    out = operations( kit, ScriptedConsole( answers=[], secrets=[]))[ "execute_playback" ].run(
        { "name": "Test Bank statement", "port": int( PORT )})
    assert out.ok, out.line + "\n" + "\n".join( f"{ r.position } { r.action_type } { r.status } { r.message }"
                                                for r in kit.run_log().run( out.data[ "run_id" ]).results )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ( downloads / "sept.pdf" ).exists():
        time.sleep( 0.2 )
    assert ( downloads / "sept.pdf" ).read_bytes() == PAGES[ "/file.pdf" ]
