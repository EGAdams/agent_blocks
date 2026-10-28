#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Navigate -- Open a URL in the debug Chrome and wait for it to load.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_navigate.py --help

Phases: code(navigate)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS

FLAGS = [
    Flag( "url", "http(s) only", required=True ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "navigate", __doc__, sys.argv[ 1: ], FLAGS ))
