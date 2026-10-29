#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Statement Request -- Download the bank statement a free-text request names, agent-safe.

"get the October 2025 Chase statement" -> the bank's templated playback
(browser_workflows/statements/sources.py), run for that month. No approvals
and no secrets: a login or a consequential step blocks and says so. This is
the post office's `statement-specialist` card (agent_comms/agents.json), which
passes the request first and reads the closing `DONE:`/`REFUSED:` line.

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_statement_request.py "October 2025 Chase statement"

Phases: code(statement_request)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation, ENDPOINT_FLAGS

FLAGS = [
    Flag( "request", "e.g. \"get the October 2025 Chase statement\"", required=True ),
    Flag( "actor", "the agent asking" ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    argv = sys.argv[ 1: ]
    if argv and not argv[ 0 ].startswith( "-" ):          # the post office's shape: the request comes first
        argv = [ "--request", *argv ]
    sys.exit( run_browser_operation( "statement_request", __doc__, argv, FLAGS ))
