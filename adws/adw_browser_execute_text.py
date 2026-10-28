#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Execute Text -- Type a value into one field in the debug Chrome (never a password field).

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_execute_text.py --help

Phases: code(execute_text)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS, GATE_FLAGS, LOCATOR_FLAGS

FLAGS = [
    *LOCATOR_FLAGS,
    Flag( "value", "the text (logged as redacted)", required=True ),
    *ENDPOINT_FLAGS,
    *GATE_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "execute_text", __doc__, sys.argv[ 1: ], FLAGS ))
