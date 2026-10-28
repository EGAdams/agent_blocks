#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Check Alerts -- List the failed tasks one agent has not yet been told about.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_check_alerts.py --agent <agent> [--acknowledge] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(check_alerts)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "agent", "whose alerts", required=True ),
    Flag( "acknowledge", "mark every listed alert as heard", kind="bool" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "check_alerts", __doc__, sys.argv[ 1: ], FLAGS ))
