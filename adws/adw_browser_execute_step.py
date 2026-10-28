#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Execute Step -- Run one step of a saved playback against the live page.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_execute_step.py --help

Phases: code(execute_step)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS, GATE_FLAGS

FLAGS = [
    Flag( "name", "the saved playback's name", required=True ),
    Flag( "position", "the step number", required=True, kind="int" ),
    *ENDPOINT_FLAGS,
    *GATE_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "execute_step", __doc__, sys.argv[ 1: ], FLAGS ))
