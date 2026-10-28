#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Request Mechanic -- Ask the mechanic to diagnose one task (or ADW run) and wait for its report.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_request_mechanic.py --sender <sender> --task-id <task_id> [--failed-adw-id <adw_id>] [--wait-seconds <wait_seconds>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(request_mechanic)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "sender", "who is asking", required=True ),
    Flag( "task_id", "the failed task", required=True ),
    Flag( "adw_id", "the failed ADW run", option="--failed-adw-id" ),
    Flag( "wait_seconds", "how long to wait", kind="float" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "request_mechanic", __doc__, sys.argv[ 1: ], FLAGS ))
