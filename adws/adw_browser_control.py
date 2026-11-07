#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Control -- one traceable browser operation per run.

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_control.py browser.open --url https://example.com
    uv run adws/adw_browser_control.py browser.tabs
    uv run adws/adw_browser_control.py browser.click --text "Notes" --browser edge

Operations: browser.open  browser.navigate  browser.refresh  browser.tabs  browser.select_tab
            browser.click  browser.read  browser.screenshot  browser.status  browser.close

It reuses what exists: browser_session starts the Windows Chrome/Edge with a DevTools port,
cdp_browser drives the page (no Selenium, no driver), browser_workflows finds the element and
classifies consequential clicks. This script only picks the operation (the first argument).
Each run is an SSSF session whose phase is the operation, so the lifecycle log and the object
trace (Object Debugger) show its ADW id, timestamps, status, arguments and result; the ADW id
is the execution id. `--json-out` writes the whole outcome. The browser window is released, never quit.

Phases: code(<operation>)
"""
import sys
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))

from browser_workflow_adw import Flag, run_browser_operation
from browser_control.catalog import PUBLIC, browser_control_operations

FLAGS = [
    Flag( "browser", "chrome (the default), edge" ),
    Flag( "url", "an http(s) address (open, navigate)" ),
    Flag( "tab", "a tab number or id from browser.tabs (select_tab, close; optional elsewhere)" ),
    Flag( "text", "the element's visible text (click)" ),
    Flag( "label", "the element's label or aria-label (click)" ),
    Flag( "css", "a CSS selector (click)" ),
    Flag( "confirm", "EG has approved a consequential click", kind="bool" ),
]

if __name__ == "__main__":
    argv = sys.argv[ 1: ]
    if not argv or argv[ 0 ] not in PUBLIC:
        sys.exit( f"usage: adw_browser_control.py <{ '|'.join( PUBLIC )}> [flags]" )
    operation, rest = PUBLIC[ argv[ 0 ]], argv[ 1: ]
    sys.exit( run_browser_operation( operation, __doc__, rest, FLAGS,
                                     catalog=lambda kit, console: browser_control_operations(),
                                     default_transport="cdp" ))
