#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Execute Click -- Click one element in the debug Chrome.

A button that looks like pay/transfer/delete/submit is refused without --yes.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_execute_click.py --help

Phases: code(execute_click)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS, GATE_FLAGS, LOCATOR_FLAGS

FLAGS = [
    *LOCATOR_FLAGS,
    *ENDPOINT_FLAGS,
    *GATE_FLAGS,
    Flag( "yes", "approve it, if it looks consequential", kind="bool" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "execute_click", __doc__, sys.argv[ 1: ], FLAGS ))
