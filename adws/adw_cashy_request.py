#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "lancedb", "pyarrow"]
# ///
"""ADW Cashy Request -- Hand Cashy one free-text request from another agent.

The post office's `cashy` card (agent_comms/agents.json) passes the request
first and reads the closing `DONE:`/`REFUSED:` line. It is the same
`cashy_turn` operation the chat uses, in its own `post_office` conversation
so an agent never steps on the person's; `--actor` names the agent, and an
agent can never approve a consequential step.

    cd /home/adamsl/agent_blocks && uv run adws/adw_cashy_request.py "status" --actor toyota

Phases: code(cashy_turn)
"""
import sys

from browser_workflow_adw import ENDPOINT_FLAGS, Flag, run_browser_operation
from cashy.operations import cashy_operations

FLAGS = [
    Flag( "text", "what to ask Cashy, e.g. \"get my September 2025 Chase statement\"", required=True ),
    Flag( "session", "which conversation (default \"post_office\")" ),
    Flag( "actor", "the agent asking -- only the person at the keyboard may approve" ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    argv = sys.argv[ 1: ]
    if argv and not argv[ 0 ].startswith( "-" ):          # the post office's shape: the request comes first
        argv = [ "--text", *argv ]
    if "--session" not in argv:
        argv += [ "--session", "post_office" ]
    sys.exit( run_browser_operation( "cashy_turn", __doc__, argv, FLAGS,
                                     catalog=cashy_operations, default_transport="cdp" ))
