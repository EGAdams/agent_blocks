"""What an agent shows while it works, and what is held back. Pure.

Two event names, so detail never leaves the machine by accident:

  task.progress  short operational steps; the bridge may relay these to the gateway
  task.output    commands, tool results, stdout/stderr, the agent's visible messages; local only

Private model reasoning is never handed to this module. A text that looks like
it carries a credential is replaced whole, never trimmed around the secret.
"""
from __future__ import annotations

import re
from collections.abc import Callable

PROGRESS = "task.progress"
OUTPUT   = "task.output"
LOCAL_PHASES = frozenset({ "command", "output", "stderr", "message", "result" })
CLIP         = 600
MAX_OUTPUT   = 500          # per task and per run; the rest are counted, not kept
WITHHELD     = "[withheld: it looked like it contained a credential]"
ENV_SECRET   = re.compile( r"(?i)\b[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY|CREDENTIAL)[A-Z0-9_]*"
                           r"\s*[=:]\s*['\"]?[^\s'\"]{6,}|\bBearer\s+[A-Za-z0-9._~+/-]{16,}" )


def event_name( phase: str ) -> str:
    return OUTPUT if phase in LOCAL_PHASES else PROGRESS


def clean( text: str, looks_secret: Callable[[ str ], object ] = lambda _: False ) -> str:
    """One bounded line, or the withheld notice."""
    if looks_secret( text ) or ENV_SECRET.search( text ):
        return WITHHELD
    flat = text.replace( "\r", "" ).strip().replace( "\n", " ⏎ " )
    return flat if len( flat ) <= CLIP else flat[ :CLIP - 1 ] + "…"
