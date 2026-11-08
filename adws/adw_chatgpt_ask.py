#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW ChatGPT Ask -- one message to a ChatGPT session, and its reply.

    cd /home/adamsl/agent_blocks && uv run adws/adw_chatgpt_ask.py --message "what county is Fralick's Addition in?"
    uv run adws/adw_chatgpt_ask.py --message "over to you" --instance bati --no-wait

This is `adw_chatgpt_relay.py relay.ask` with the operation pinned, so a caller that only ever asks one
thing does not have to name an operation. It is deliberately a separate script rather than an argument:
a card in the post office, a tool in the gateway and a plan in Toyota's registry each want one command
with one job, and this is the cheapest way to give them that. When the relay is debugged, scripts like
this one are the first thing to collapse back into the multi-operation ADW -- nothing but this file and
its one line in the docs would change.

Phases: code(relay_ready) code(relay_guard) code(relay_snapshot) code(relay_type) code(relay_send)
        code(relay_settle) code(relay_answer)
"""
import sys
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))

from adw_chatgpt_relay import FLAGS
from browser_workflow_adw import run_browser_operation
from chatgpt_relay.catalog import relay_operations

if __name__ == "__main__":
    sys.exit( run_browser_operation( "relay_ask", __doc__, sys.argv[ 1: ], FLAGS,
                                     catalog=lambda kit, console: relay_operations(),
                                     default_transport="cdp" ))
