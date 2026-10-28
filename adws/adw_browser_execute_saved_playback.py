#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Execute Saved Playback -- Load, validate, then replay a saved playback -- three phases.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_execute_saved_playback.py --help

Phases: code(load_recording) -> code(validate_recording) -> code(execute_playback)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS, GATE_FLAGS

FLAGS = [
    Flag( "name", "the saved playback's name", required=True ),
    Flag( "from_step", "start at this step (resume)", kind="int" ),
    Flag( "actor", "who asked (default user)" ),
    *ENDPOINT_FLAGS,
    *GATE_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "execute_saved_playback", __doc__, sys.argv[ 1: ], FLAGS ))
