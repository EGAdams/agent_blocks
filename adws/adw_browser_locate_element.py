#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Locate Element -- Find one element on the debug Chrome's page by its locators; touch nothing.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_locate_element.py --help

Phases: code(locate_element)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS, LOCATOR_FLAGS

FLAGS = [
    *LOCATOR_FLAGS,
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "locate_element", __doc__, sys.argv[ 1: ], FLAGS ))
