"""Live: drive a real Chrome over CDP on a local test site. No screenshots.

Runs only with CDP_TEST_PORT set to a debug Chrome's port on this machine,
for example a throwaway headless one:

    google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/x about:blank
    CDP_TEST_PORT=9333 uv run --with pydantic --with pytest pytest -q cdp_browser/tests
"""
from __future__ import annotations

import os
import tempfile
import urllib.request
import time
from pathlib import Path

import pytest

from browser_session.models import DebugEndpoint
from cdp_browser.provider import default_cdp_provider

from .live_site import PAGES, site  # noqa: F401  (a fixture)

PORT = os.environ.get( "CDP_TEST_PORT" )
pytestmark = pytest.mark.skipif( not PORT, reason="set CDP_TEST_PORT to a local debug Chrome" )

def _until( predicate, seconds: float = 10.0 ) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep( 0.2 )
    return False


def test_read_click_type_go_back_and_download( site: str ) -> None:
    endpoint = DebugEndpoint( port=int( PORT ))
    browser  = default_cdp_provider().attach( endpoint )
    try:
        browser.open_tab( site + "/" )
        assert browser.title == "Test Bank"

        options = browser.interactive_elements()
        texts   = [ o.text for o in options ]
        assert "Statements" in texts and "Transfer Activity" in texts and "Hidden" not in texts
        assert all( o.attrs.get( "value" ) is None for o in options )         # typed values never read

        field = browser.find( "#q" )[ 0 ]
        field.send_keys( "gas at church" )
        browser.select_option( browser.find( "#m" )[ 0 ], "October" )
        assert browser.find( "#m option:checked" )[ 0 ].text == "October"
        browser.press_key( field, "Enter" )
        assert _until( lambda: "/search" in browser.current_url )
        assert "q=gas+at+church" in browser.current_url

        browser.go_back()
        assert _until( lambda: browser.title == "Test Bank" )
        link = browser.find( "a#stm" )[ 0 ]
        assert link.is_displayed() and link.get_attribute( "href" ) == "/statements"
        assert not browser.find( "a[href='/hidden']" )[ 0 ].is_displayed()
        link.click()
        assert _until( lambda: browser.title == "Statements" )
        assert "September 2025 statement" in browser.body_text()

        with tempfile.TemporaryDirectory() as folder:
            browser.configure_downloads( folder )
            browser.find( "#dl" )[ 0 ].click()
            saved = Path( folder ) / "sept.pdf"
            assert _until( lambda: saved.exists() and saved.read_bytes() == PAGES[ "/file.pdf" ])

        assert browser.screenshot( "/tmp/never.png" ) is False
        assert any( t.handle == browser.target_id for t in browser.tabs())
    finally:
        target = browser.target_id
        browser.release()
        urllib.request.urlopen( f"http://127.0.0.1:{ PORT }/json/close/{ target }" ).read()   # the test's own tab
