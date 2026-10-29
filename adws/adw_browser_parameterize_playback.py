#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Parameterize Playback -- Turn one month's recording into a playback for any month.

Shows what would change (the month and year become blanks, the day a wildcard,
row-pinned locators dropped); --new-name NAME --yes saves that as a new
playback. The recording itself is never changed.

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_parameterize_playback.py \\
        --name download_sept_2025_statement --example "September 2025" \\
        --new-name chase_statement_by_month --yes

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md).

Phases: code(parameterize_playback)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "name", "the recorded playback", required=True ),
    Flag( "example", "the month it recorded, e.g. \"September 2025\" (default: read from its name)" ),
    Flag( "new_name", "save the template under this name" ),
    Flag( "yes", "save it (without this, only show the proposal)", kind="bool" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "parameterize_playback", __doc__, sys.argv[ 1: ], FLAGS ))
