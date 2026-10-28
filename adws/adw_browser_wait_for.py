#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Wait For -- Wait for one explicit condition in the debug Chrome.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_wait_for.py --help

Phases: code(wait_for)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS

FLAGS = [
    Flag( "kind", "url_equals, url_contains, text_present, navigation_complete, element_visible, element_enabled" ),
    Flag( "value", "the URL, text or CSS selector" ),
    Flag( "timeout", "seconds (default 15)", kind="float" ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "wait_for", __doc__, sys.argv[ 1: ], FLAGS ))
