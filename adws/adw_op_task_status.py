#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Task Status -- Report where one task is, or every task a sender has open.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_task_status.py [--sender <sender>] [--task-id <task_id>] [--limit <limit>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(task_status)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "sender", "whose tasks" ),
    Flag( "task_id", "one task" ),
    Flag( "limit", "how many", kind="int" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "task_status", __doc__, sys.argv[ 1: ], FLAGS ))
