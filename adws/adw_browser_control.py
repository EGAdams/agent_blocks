#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Control -- one traceable browser operation per run.

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_control.py browser.open --url https://example.com
    uv run adws/adw_browser_control.py browser.tabs
    uv run adws/adw_browser_control.py browser.click --text "Notes"
    uv run adws/adw_browser_control.py browser.form_controls
    uv run adws/adw_browser_control.py browser.select_option --css "#ddlType" --option_text "PLAT Records"
    uv run adws/adw_browser_control.py browser.fill --css "#txtName" --text "Fralick's Addition"
    uv run adws/adw_browser_control.py browser.download --text "0337201.tif"

Operations: browser.open  browser.navigate  browser.refresh  browser.tabs  browser.select_tab
            browser.click  browser.read  browser.screenshot  browser.status  browser.close
            browser.form_controls  browser.select_option  browser.fill  browser.download

browser.download is the one that saves a file. A click cannot: Chrome writes a download only when a
folder has been set for the session, which that operation does and browser.click deliberately does not,
so browser.click refuses a file link and names it instead. adw_browser_download.py is the same operation
on its own, for when downloading is the whole job.

It reuses what exists: browser_session starts the Windows Chrome with a DevTools port,
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
    Flag( "browser", "chrome (the only authorized browser; anything else is refused)" ),
    Flag( "url", "an http(s) address (open, navigate)" ),
    Flag( "tab", "a tab number or id from browser.tabs (select_tab, close; optional elsewhere)" ),
    Flag( "text", "the element's visible text (click), or the words to type (fill)" ),
    Flag( "label", "the element's label or aria-label (click, select_option, fill)" ),
    Flag( "css", "a CSS selector, best taken from browser.form_controls (click, select_option, fill)" ),
    Flag( "option_text", "the dropdown item as it reads on the screen (select_option)" ),
    Flag( "value", "the dropdown item's own value, matched exactly (select_option)" ),
    Flag( "limit", "how many controls to list, 1-200, default 60 (form_controls)" ),
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
