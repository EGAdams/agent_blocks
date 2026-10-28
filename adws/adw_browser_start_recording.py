#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Start Recording -- Start recording the selected Chrome tab (starts the recorder service if it is down).

The extension must be loaded in that Chrome; the tab gets a red RECORDING badge.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_start_recording.py --help

Phases: code(start_recording)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS: list[ Flag ] = []

if __name__ == "__main__":
    sys.exit( run_browser_operation( "start_recording", __doc__, sys.argv[ 1: ], FLAGS ))
