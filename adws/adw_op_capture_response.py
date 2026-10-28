#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Capture Response -- Wait for the reply to one task and take it out of the sender's inbox.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_capture_response.py --sender <sender> --task-id <task_id> [--wait-seconds <wait_seconds>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(capture_response)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "sender", "who sent the task", required=True ),
    Flag( "task_id", "the task", required=True ),
    Flag( "wait_seconds", "how long to wait", kind="float" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "capture_response", __doc__, sys.argv[ 1: ], FLAGS ))
