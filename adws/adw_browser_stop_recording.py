#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Stop Recording -- Stop recording; the session waits to be saved (Save As) or discarded.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_stop_recording.py --help

Phases: code(stop_recording)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS: list[ Flag ] = []

if __name__ == "__main__":
    sys.exit( run_browser_operation( "stop_recording", __doc__, sys.argv[ 1: ], FLAGS ))
