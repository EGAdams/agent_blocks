#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Execute Task -- Work one agent's inbox: claim queued requests, run them on that agent's executor, reply. The post office spawns this, detached, whenever it delivers work.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_execute_task.py --agent <agent> [--task-id <task_id>] [--recover-after-seconds <recover_after_seconds>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(execute_task)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "agent", "whose inbox to work", required=True ),
    Flag( "task_id", "only this task" ),
    Flag( "recover_after_seconds", "first settle tasks a dead worker left running longer than this", kind="float" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "execute_task", __doc__, sys.argv[ 1: ], FLAGS ))
