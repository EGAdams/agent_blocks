"""Live: the highlight probe on a page running the dashboard's own blink animation. No screenshots.

    google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/x about:blank
    CDP_TEST_PORT=9333 uv run --with pydantic --with pytest pytest -q cdp_browser/tests/test_cdp_highlights_live.py
"""
from __future__ import annotations

import hashlib
import os

import pytest

from browser_control.highlights import blinking, classify_page
from browser_session.models import DebugEndpoint
from cdp_browser.provider import default_cdp_provider

from .live_site import site  # noqa: F401  (a fixture)

PORT = os.environ.get( "CDP_TEST_PORT" )
pytestmark = pytest.mark.skipif( not PORT, reason="set CDP_TEST_PORT to a local debug Chrome" )


def test_the_blinking_line_is_found_without_being_described( site: str ) -> None:
    browser = default_cdp_provider().attach( DebugEndpoint( port=int( PORT )))
    try:
        browser.open_tab( site + "/notes" )
        found = browser.highlights( "#p", sample_ms=1600 )
        assert found[ "root_found" ] and found[ "total" ] >= 3
        states = { s.text: s for s in classify_page( found )}
        active = states[ "[VERIFY PLAT RECORD REFERENCE: Liber 4]" ]
        assert active.blinking and active.text_visible and active.animation == "notes-active-deed-warning"
        assert "yellow" in " ".join( active.evidence ) and "none" in " ".join( active.evidence )
        steady = states[ "[VERIFY TITLE EXCEPTIONS: list]" ]
        assert steady.highlighted and not steady.blinking and steady.text_visible
        assert [ s.text for s in blinking( list( states.values()))] == [ active.text ]
        assert "Plain words." not in states

        digest = browser.text_digest( "#t" )
        assert digest[ "chars" ] == 10
        assert digest[ "sha256" ] in ( None, hashlib.sha256( b"saved text" ).hexdigest())
        assert browser.text_digest( "#nope" ) == { "found": False }
        assert browser.highlights( "#nope" )[ "root_found" ] is False
    finally:
        browser.release()
