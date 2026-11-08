#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW ChatGPT Relay -- one traceable message into a standing ChatGPT session, and its reply back.

    cd /home/adamsl/agent_blocks && uv run adws/adw_chatgpt_relay.py relay.instances
    uv run adws/adw_chatgpt_relay.py relay.health
    uv run adws/adw_chatgpt_relay.py relay.ask --message "what county is Fralick's Addition in?"
    uv run adws/adw_chatgpt_relay.py relay.ask --message "over to you" --no-wait
    uv run adws/adw_chatgpt_relay.py relay.read --last 6

Operations: relay.instances  relay.health  relay.ready  relay.open  relay.read  relay.new_chat
            relay.type  relay.send  relay.snapshot  relay.guard  relay.settle  relay.answer  relay.ask

`relay.ask` is the one to use: ready -> guard -> snapshot -> type -> send -> settle -> answer, each
step its own lifecycle phase, so a failed ask names the step that failed. The other twelve exist because
debugging a relay means running one step at a time.

The session on the other side is a person's real, signed-in ChatGPT account. Three consequences are
enforced here rather than asked for: there is no quit (it would discard the signed-in profile), no
credential is ever typed (it would land in that account's permanent history), and --new-chat refuses
without --confirm (it would leave the current thread's context behind). `--message` is logged as
"(redacted)", never as what was said.

Phases: code(<operation>), or seven of them for relay.ask.
"""
import sys
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))

from browser_workflow_adw import Flag, run_browser_operation
from chatgpt_relay.catalog import PUBLIC, relay_operations

FLAGS = [
    Flag( "instance", "which ChatGPT session (relay.instances lists them; default bati)" ),
    Flag( "message", "what to say (ask, type, guard)" ),
    Flag( "url", "where to point that Chrome (open; default https://chatgpt.com/)" ),
    Flag( "last", "how many turns to read, 1-100 (read)", kind="int" ),
    Flag( "settle", "how to wait for the reply: stable_text (default), new_turn, none" ),
    Flag( "timeout", "seconds to wait for the reply before reporting it partial", kind="float" ),
    Flag( "stable_seconds", "seconds the reply must stop changing to count as finished", kind="float" ),
    Flag( "no_wait", "deliver the message and end without waiting for a reply", kind="bool" ),
    Flag( "hops", "how many relays this message has already crossed (default 0)", kind="int" ),
    Flag( "origin", "which session this message came from, so it is never delivered back there" ),
    Flag( "confirm", "yes, start a new chat and leave the current thread's context behind", kind="bool" ),
]

if __name__ == "__main__":
    argv = sys.argv[ 1: ]
    if not argv or argv[ 0 ] not in PUBLIC:
        sys.exit( f"usage: adw_chatgpt_relay.py <{ '|'.join( PUBLIC )}> [flags]" )
    operation, rest = PUBLIC[ argv[ 0 ]], argv[ 1: ]
    sys.exit( run_browser_operation( operation, __doc__, rest, FLAGS,
                                     catalog=lambda kit, console: relay_operations(),
                                     default_transport="cdp" ))
