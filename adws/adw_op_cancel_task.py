#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Cancel Task -- Withdraw a queued task before any worker claims it.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_cancel_task.py --task-id <task_id> [--sender <sender>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(cancel_task)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "task_id", "the task", required=True ),
    Flag( "sender", "who is cancelling" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "cancel_task", __doc__, sys.argv[ 1: ], FLAGS ))
