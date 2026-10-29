"""Exercise recorded locators and playback in an isolated real browser.

No extension is loaded and no connection is made to the live recorder.
Run with BW_E2E=1; uses the cached Chrome for Testing.
"""
from __future__ import annotations

import os

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
