#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "lancedb", "pyarrow"]
# ///
"""ADW Cashy Find Transaction -- Open transfer activity for a payee after checking it against your transfer records. Opens; never starts a transfer.

    cd /home/adamsl/agent_blocks && uv run adws/adw_cashy_find_transaction.py --port 9222

One Cashy operation as one ADW run, in-process, over the DevTools protocol
(see cashy/DESIGN.md).

Phases: code(cashy_find_transaction)
"""
import sys

from browser_workflow_adw import ENDPOINT_FLAGS, Flag, run_browser_operation
from cashy.operations import cashy_operations

FLAGS = [
    Flag( "target", "the words on the element, or the payee" ),
    Flag( "account", "the account, masked, e.g. •6285" ),
    Flag( "bank", "the bank, e.g. Chase" ),
    Flag( "session", "which conversation (default \"default\")" ),
    Flag( "actor", "who is asking: user (default), toyota, sydney, ... -- only the user may approve" ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "cashy_find_transaction", __doc__, sys.argv[ 1: ], FLAGS,
                                     catalog=cashy_operations, default_transport="cdp" ))
