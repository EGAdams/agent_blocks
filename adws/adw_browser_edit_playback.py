#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Edit Playback -- Edit a saved playback: rename, duplicate, delete, move, remove, insert, locators, value, waits, approval, url.

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_edit_playback.py --help

Phases: code(edit_playback)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, LOCATOR_FLAGS

FLAGS = [
    Flag( "name", "the saved playback's name", required=True ),
    Flag( "edit", "which edit", required=True ),
    Flag( "position", "the step number", kind="int" ),
    Flag( "to", "move: the new step number", kind="int" ),
    Flag( "new_name", "rename / duplicate: the new name" ),
    Flag( "value", "value: the new text (never a secret step)" ),
    Flag( "waits", "waits: a JSON list of conditions" ),
    Flag( "action", "insert: the step, as JSON" ),
    Flag( "required", "approval: yes or no" ),
    Flag( "url", "url: the new http(s) URL" ),
    Flag( "yes", "delete: confirm", kind="bool" ),
    *LOCATOR_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "edit_playback", __doc__, sys.argv[ 1: ], FLAGS ))
