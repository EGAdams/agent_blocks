#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Download -- press one element in the debug Chrome and keep the file it saves.

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_download.py --css "a#attachmentList_gdvAttachmentList_ctl02_lnkFileName"
    uv run adws/adw_browser_download.py --text "0337201.tif" --tab 1
    uv run adws/adw_browser_download.py --label "Download statement" --confirm

This is the operation browser.click is deliberately not. Chrome writes a downloaded file only
when a download folder has been set for the session, and browser.click never sets one -- so a
click on a `.tif` anchor reported "Clicked '0337201.tif'." with an empty folder behind it, which
is a true sentence about a failed job. Saving is its own operation so that writing to EG's disk
is the thing that was asked for, named in the task and visible in the audit log, rather than a
power all thirteen other operations quietly acquired.

It reuses what exists and adds nothing: browser_session started the Chrome, cdp_browser drives the
page and sets the download behaviour over the browser-level socket, the element is found by the
same ranked resolver as a click, the folder is the one browser_workflows already downloads into
(BROWSER_WORKFLOW_DOWNLOAD_DIR moves it), and the waiting and checking are the watcher and verifier
Cashy uses for bank statements. The run is an SSSF session whose phase is the operation, so the
lifecycle log and the object trace carry the ADW id, the arguments and the result; `--json-out`
writes the whole outcome. The browser window is released, never quit.

A press whose words look consequential (pay, transfer, delete) is refused without --confirm, the
same rule as browser.click. A file that arrives empty, or claims to be a PDF and is not, is
reported as a problem rather than as a download; and if no file arrives at all the run says so and
names what the click did instead, which is what tells you which half failed.

Phases: code(browser_download)
"""
import sys
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))

from browser_workflow_adw import Flag, run_browser_operation
from browser_control.catalog import browser_control_operations
from browser_control.downloads import Download

FLAGS = [
    Flag( "browser", "chrome (the only authorized browser; anything else is refused)" ),
    Flag( "tab", "a tab number or id from browser.tabs (optional; default is the active tab)" ),
    Flag( "css", "a CSS selector for the link or button that saves the file (best, from browser.form_controls)" ),
    Flag( "label", "the element's label or aria-label" ),
    Flag( "text", "the element's visible text, e.g. the file name as the page shows it" ),
    Flag( "confirm", "EG has approved a consequential press", kind="bool" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( Download.name, __doc__, sys.argv[ 1: ], FLAGS,
                                     catalog=lambda kit, console: browser_control_operations(),
                                     default_transport="cdp" ))
