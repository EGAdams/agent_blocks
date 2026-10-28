#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Retry Task -- Send a failed or cancelled task again as a new, linked task. Work that may have changed files needs --confirm.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_retry_task.py --task-id <task_id> [--confirm] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(retry_task)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "task_id", "the failed task", required=True ),
    Flag( "confirm", "re-send even work that is not idempotent", kind="bool" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "retry_task", __doc__, sys.argv[ 1: ], FLAGS ))
