#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Execute Playback -- Replay a saved playback in the debug Chrome.

Consequential steps need --interactive or --approve; secrets need --interactive.
Pause/stop it from another terminal with adw_browser_control_playback.py.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_execute_playback.py --help

Phases: code(execute_playback)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS, GATE_FLAGS

FLAGS = [
    Flag( "name", "the saved playback's name", required=True ),
    Flag( "from_step", "start at this step (resume)", kind="int" ),
    Flag( "actor", "who asked (default user)" ),
    Flag( "period", "fill a templated playback's month, e.g. \"October 2025\"" ),
    Flag( "params", "fill a templated playback's blanks by name, a JSON object", kind="json" ),
    *ENDPOINT_FLAGS,
    *GATE_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "execute_playback", __doc__, sys.argv[ 1: ], FLAGS ))
