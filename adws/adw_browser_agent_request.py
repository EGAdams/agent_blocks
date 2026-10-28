#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Agent Request -- Run the saved playback a free-text request names, agent-safe.

No approvals and no secrets: anything consequential or secret blocks and says so.
This is what Toyota, Sydney and the Mechanic call, through the post office's
`browser-recorder` card (agent_comms/agents.json), which passes the request as the
first argument and reads the closing `DONE:`/`REFUSED:` line (`--verdict`).

One browser-workflow operation as one ADW run (see browser_workflow_adw.py
and browser_workflows/DESIGN.md):

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_agent_request.py --help

Phases: code(agent_request)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS

FLAGS = [
    Flag( "request", "e.g. \"run pay bill\"", required=True ),
    Flag( "actor", "the agent asking" ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    argv = sys.argv[ 1: ]
    if argv and not argv[ 0 ].startswith( "-" ):          # the post office's shape: the request comes first
        argv = [ "--request", *argv ]
    sys.exit( run_browser_operation( "agent_request", __doc__, argv, FLAGS ))
