#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Validate Task -- Check a request (JSON) against the registry: known agents, a real capability, no loop. Sends nothing.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_validate_task.py --request <request> \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(validate_task)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "request", "the TaskRequest as JSON", required=True, kind="json" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "validate_task", __doc__, sys.argv[ 1: ], FLAGS ))
