#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Delegate Task -- Composite: route a free-text request -> validate -> send -> capture the reply (skipped when the work is asynchronous). Each step is its own phase.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_delegate_task.py --sender <sender> --text <text> [--wait-seconds <wait_seconds>] [--priority <priority>] [--context <context>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(route_request) -> code(validate_task) -> code(send_message) [-> code(capture_response)]
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "sender", "who is asking", required=True ),
    Flag( "text", "the request, naming the agent", required=True ),
    Flag( "wait_seconds", "override how long to wait", kind="float" ),
    Flag( "priority", "00-99", kind="int" ),
    Flag( "context", "a JSON object, e.g. {'workdir': '/home/adamsl/letta-code'}", kind="json" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "delegate_task", __doc__, sys.argv[ 1: ], FLAGS ))
