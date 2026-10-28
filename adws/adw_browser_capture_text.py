#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Capture Text -- Record one completed text entry into the open session by hand.

A password-like field is stored as a secret with no value, whatever --value says.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_capture_text.py --help

Phases: code(capture_text)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, LOCATOR_FLAGS

FLAGS = [
    *LOCATOR_FLAGS,
    Flag( "value", "what was typed (logged as redacted)", required=True ),
    Flag( "url", "the page" ),
    Flag( "field_name", "the field's name attribute" ),
    Flag( "input_type", "text, email, ... (default text)" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "capture_text", __doc__, sys.argv[ 1: ], FLAGS ))
