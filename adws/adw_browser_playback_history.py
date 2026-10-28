#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Playback History -- Recent playback runs, or one run step by step with its evidence.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_playback_history.py --help

Phases: code(playback_history)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "name", "only this playback's runs" ),
    Flag( "run_id", "one run in detail", kind="int" ),
    Flag( "limit", "how many (default 20)", kind="int" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "playback_history", __doc__, sys.argv[ 1: ], FLAGS ))
