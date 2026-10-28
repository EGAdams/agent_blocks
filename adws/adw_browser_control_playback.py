#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Control Playback -- Pause, resume or stop a running playback.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_control_playback.py --help

Phases: code(control_playback)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "run_id", "the run", required=True, kind="int" ),
    Flag( "command", "pause, resume or stop", required=True ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "control_playback", __doc__, sys.argv[ 1: ], FLAGS ))
