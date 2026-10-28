#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Capture Click -- Record one click into the open session by hand (no extension needed).

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_capture_click.py --help

Phases: code(capture_click)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, LOCATOR_FLAGS

FLAGS = [
    *LOCATOR_FLAGS,
    Flag( "url", "the page it happened on" ),
    Flag( "tag", "the element's tag (default button)" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "capture_click", __doc__, sys.argv[ 1: ], FLAGS ))
