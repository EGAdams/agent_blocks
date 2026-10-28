#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Repair Locator -- Show the Mechanic's proposed locator repairs for a run, or apply one.

Only a repair that was verified on the live page can be saved, and only with --yes.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_repair_locator.py --help

Phases: code(repair_locator)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "run_id", "list this run's proposals", kind="int" ),
    Flag( "apply", "the repair id to save", kind="int" ),
    Flag( "yes", "confirm saving it", kind="bool" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "repair_locator", __doc__, sys.argv[ 1: ], FLAGS ))
