#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Log Result -- Record a result or note against a task on the post office event log.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_log_result.py --agent <agent> [--task-id <task_id>] [--note <note>] [--event <event>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(log_result)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "agent", "who is logging", required=True ),
    Flag( "task_id", "the task" ),
    Flag( "note", "what to record" ),
    Flag( "event", "event name, default task.note" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "log_result", __doc__, sys.argv[ 1: ], FLAGS ))
