#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Discard Recording -- Throw away the stopped recording without saving it.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_discard_recording.py --help

Phases: code(discard_recording)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS: list[ Flag ] = []

if __name__ == "__main__":
    sys.exit( run_browser_operation( "discard_recording", __doc__, sys.argv[ 1: ], FLAGS ))
