#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Save Recording -- Save the stopped recording as a named playback.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_save_recording.py --help

Phases: code(save_recording)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "name", "the new playback's name", required=True ),
    Flag( "description", "what it is for" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "save_recording", __doc__, sys.argv[ 1: ], FLAGS ))
