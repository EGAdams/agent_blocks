"""Exercise the extension's locator JavaScript in an isolated real browser.

No extension is loaded and no connection is made to the live recorder.
Run with BW_E2E=1; uses the cached Chrome for Testing.
"""
from __future__ import annotations

import os

import pytest

from .test_e2e_chrome import CHROME, DRIVER, EXTENSION


@pytest.mark.skipif( not os.environ.get( "BW_E2E" ) or CHROME is None,
                     reason="set BW_E2E=1 (needs cached Chrome for Testing)" )
def test_background_and_child_css_locators_resolve_to_the_original_elements():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.binary_location = str( CHROME )
    options.add_argument( "--headless=new" )
    options.add_argument( "--disable-gpu" )
    driver = webdriver.Chrome( options=options, service=Service( str( DRIVER )))
    try:
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
    finally:
        driver.quit()
