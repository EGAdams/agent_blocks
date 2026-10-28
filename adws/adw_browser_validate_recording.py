#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Validate Recording -- Check a saved playback can run, without running it.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_validate_recording.py --help

Phases: code(validate_recording)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "name", "the saved playback's name", required=True ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "validate_recording", __doc__, sys.argv[ 1: ], FLAGS ))
