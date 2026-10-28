#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Create Message -- Build a structured task request from explicit fields. Sends nothing.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_create_message.py --sender <sender> --recipient <recipient> --task-type <task_type> --text <text> [--priority <priority>] [--wait-seconds <wait_seconds>] [--context <context>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(create_message)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "sender", "who is asking", required=True ),
    Flag( "recipient", "which agent", required=True ),
    Flag( "task_type", "build, plan, review, ask, message, diagnose, repair ...", required=True ),
    Flag( "text", "the request", required=True ),
    Flag( "priority", "00-99, lower first", kind="int" ),
    Flag( "wait_seconds", "0 = asynchronous", kind="float" ),
    Flag( "context", "a JSON object", kind="json" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "create_message", __doc__, sys.argv[ 1: ], FLAGS ))
