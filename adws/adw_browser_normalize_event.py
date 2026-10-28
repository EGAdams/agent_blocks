#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Normalize Event -- Show what one raw browser event becomes as a saved step (scrubbed, normalized).

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_normalize_event.py --help

Phases: code(normalize_event)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "event", "the raw event, as JSON", required=True, kind="json" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "normalize_event", __doc__, sys.argv[ 1: ], FLAGS ))
