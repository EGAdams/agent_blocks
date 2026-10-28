#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Send And Capture -- Composite: create an explicit request -> validate -> send -> capture the reply. Each step is its own phase.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_send_and_capture.py --sender <sender> --recipient <recipient> --task-type <task_type> --text <text> [--priority <priority>] [--wait-seconds <wait_seconds>] [--context <context>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(create_message) -> code(validate_task) -> code(send_message) [-> code(capture_response)]
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "sender", "who is asking", required=True ),
    Flag( "recipient", "which agent", required=True ),
    Flag( "task_type", "the kind of work", required=True ),
    Flag( "text", "the request", required=True ),
    Flag( "priority", "00-99", kind="int" ),
    Flag( "wait_seconds", "0 = asynchronous", kind="float" ),
    Flag( "context", "a JSON object", kind="json" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "send_and_capture", __doc__, sys.argv[ 1: ], FLAGS ))
