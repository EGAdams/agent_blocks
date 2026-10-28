#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Report Failure -- Publish a failure (a post-office task or an ADW run) so the mechanic is told and diagnoses it.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_report_failure.py --agent <agent> [--task-id <task_id>] [--failed-adw-id <adw_id>] [--error <error>] \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(report_failure)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "agent", "who failed", required=True ),
    Flag( "task_id", "the task" ),
    Flag( "adw_id", "the failed ADW run", option="--failed-adw-id" ),
    Flag( "error", "what went wrong" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "report_failure", __doc__, sys.argv[ 1: ], FLAGS ))
