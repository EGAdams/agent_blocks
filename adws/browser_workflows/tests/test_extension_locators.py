"""Exercise recorded locators and playback in an isolated real browser.

No extension is loaded and no connection is made to the live recorder.
Run with BW_E2E=1; uses the cached Chrome for Testing.
"""
from __future__ import annotations

import os
import http.server
import threading
import time

import pytest

from .test_e2e_chrome import CHROME, DRIVER, EXTENSION


pytestmark = pytest.mark.skipif( not os.environ.get( "BW_E2E" ) or CHROME is None,
                                reason="set BW_E2E=1 (needs cached Chrome for Testing)" )


@pytest.fixture
def driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.binary_location = str( CHROME )
    options.add_argument( "--headless=new" )
    options.add_argument( "--disable-gpu" )
    options.add_experimental_option( "prefs", { "download.prompt_for_download": True })
    driver = webdriver.Chrome( options=options, service=Service( str( DRIVER )))
    try:
        yield driver
    finally:
        driver.quit()


def test_background_and_child_css_locators_resolve_to_the_original_elements( driver ):
    driver.get( "data:text/html,<body><div><span>" + "background " * 10 + "</span></div></body>" )
    driver.execute_script(( EXTENSION / "content" / "locator_describer.js" ).read_text())
    for target in ( "body", "span" ):
        result = driver.execute_script( """
            const el = document.querySelector(arguments[0]);
            const locators = __bw.describeLocators(el);
            return locators.filter(l => l.strategy === 'css').map(l => ({
                value: l.value, matches: document.querySelectorAll(l.value).length,
                correct: document.querySelector(l.value) === el
            }));
        """, target )
        assert result and all( r[ "matches" ] == 1 and r[ "correct" ] for r in result ), result
        if target == "body":
            assert result[ 0 ][ "value" ] == "body"


def test_recorded_zero_size_icon_replays_through_its_visible_text_link( driver, tmp_path ):
    from selenium.webdriver.common.by import By

    from browser_workflows.kit import WorkflowKit
    from browser_workflows.models import LocatorCandidate, RecordedAction
    from browser_workflows.playback.gates import DenyApprovalGate, NoSecretSource
    from browser_workflows.playback.selenium_browser import SeleniumWorkflowBrowser

    # Build nested anchors via the DOM, as component frameworks do. The HTML
    # parser would rewrite them. The painted icon is outside the inner box.
    driver.get( "data:text/html,<body></body>" )
    driver.execute_script( """
        const link = document.createElement('a');
        link.id = 'statement-menu';
        link.href = 'javascript:void(0)';
        link.style = 'display:inline-block;width:14px;height:20px;position:relative';
        const icon = document.createElement('a');
        icon.id = 'statement-icon';
        icon.href = 'javascript:void(0)';
        const label = document.createElement('span');
        label.textContent = 'Sep 12, 2025 Opens menu to save document';
        label.style = 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)';
        const picture = document.createElement('i');
        picture.style = 'position:absolute;left:0;top:0;width:14px;height:20px;background:black';
        icon.append(label, picture);
        link.append(icon);
        document.body.append(link);
        window.menuClicks = 0;
        link.addEventListener('click', () => window.menuClicks++);
    """ )
    icon = driver.find_element( By.ID, "statement-icon" )
    assert icon.is_displayed() and icon.rect[ "width" ] == icon.rect[ "height" ] == 0
    driver.execute_script(( EXTENSION / "content" / "locator_describer.js" ).read_text())
    locators = [ LocatorCandidate.model_validate( row ) for row in driver.execute_script(
        "return __bw.describeLocators(document.getElementById('statement-icon'))" )]

    class Manager:
        def find_elements( self, css ):
            return driver.find_elements( By.CSS_SELECTOR, css )

    kit = WorkflowKit( db_path=tmp_path / "db.sqlite" )
    recording = kit.repository().create( "Statement", [ RecordedAction( action_type="click", locators=locators )])
    result = kit.step_runner( DenyApprovalGate(), NoSecretSource()).run(
        SeleniumWorkflowBrowser( Manager(), None ), recording.steps[ 0 ], recording, run_id=1 )
    assert result.status == "ok", result.message
    assert result.strategy.startswith( "text:" )
    assert driver.execute_script( "return window.menuClicks" ) == 1


@pytest.mark.parametrize( "new_tab", [ False, True ])
def test_playback_saves_pdf_without_a_dialog_even_when_chrome_is_set_to_prompt( driver, tmp_path, new_tab ):
    from types import SimpleNamespace
    from selenium.webdriver.common.by import By

    from browser_workflows.kit import WorkflowKit
    from browser_workflows.operations.catalog import operations
    from browser_workflows.models import LocatorCandidate, RecordedAction
    from browser_workflows.playback.lease import FixedBrowserLease
    from browser_workflows.playback.selenium_browser import SeleniumWorkflowBrowser
    from .fakes import ScriptedConsole

    pdf = b"%PDF-1.4\n% Local download regression fixture\n%%EOF\n"

    class Handler( http.server.BaseHTTPRequestHandler ):
        def do_GET( self ):
            self.send_response( 200 )
            if self.path == "/statement":
                self.send_header( "Content-Type", "application/pdf" )
                self.send_header( "Content-Disposition", 'attachment; filename="statement.pdf"' )
                content = pdf
            else:
                self.send_header( "Content-Type", "text/html" )
                target = "target='_blank'" if new_tab else ""
                content = f"<a id='download' href='/statement' { target }>Download statement</a>".encode()
            self.send_header( "Content-Length", str( len( content )))
            self.end_headers()
            self.wfile.write( content )

        def log_message( self, *args ):
            pass

    server = http.server.ThreadingHTTPServer(( "127.0.0.1", 0 ), Handler )
    threading.Thread( target=server.serve_forever, daemon=True ).start()

    class Manager:
        def find_elements( self, css ):
            return driver.find_elements( By.CSS_SELECTOR, css )

    Manager.driver = driver
    browser = SeleniumWorkflowBrowser( Manager(), None )

    class Kit( WorkflowKit ):
        def attachment( self ):
            return SimpleNamespace( open=lambda endpoint: FixedBrowserLease( browser ))

        def download_directory( self ):
            return str( tmp_path / "downloads" )

    try:
        kit = Kit( db_path=tmp_path / "db.sqlite" )
        kit.repository().create( "Download statement", [
            RecordedAction( action_type="navigate", url=f"http://127.0.0.1:{ server.server_port }/" ),
            RecordedAction( action_type="click", locators=[ LocatorCandidate( strategy="css", value="#download" )]),
        ])
        result = operations( kit, ScriptedConsole())[ "execute_saved_playback" ].run({ "name": "Download statement" })
        assert result.ok, result.line
        downloaded = tmp_path / "downloads" / "statement.pdf"
        deadline = time.monotonic() + 10
        while not downloaded.exists() and time.monotonic() < deadline:
            time.sleep( 0.1 )
        assert downloaded.read_bytes() == pdf
        assert result.data[ "download_directory" ] == str( downloaded.parent )
    finally:
        server.shutdown()
        server.server_close()
