#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Recorder Status -- Is the recorder service up, and what is it doing?

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_recorder_status.py --help

Phases: code(recorder_status)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS: list[ Flag ] = []

if __name__ == "__main__":
    sys.exit( run_browser_operation( "recorder_status", __doc__, sys.argv[ 1: ], FLAGS ))
