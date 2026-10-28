#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Load Recording -- One saved playback's steps (secrets shown as asked-at-playback).

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_load_recording.py --help

Phases: code(load_recording)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "name", "the saved playback's name", required=True ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "load_recording", __doc__, sys.argv[ 1: ], FLAGS ))
