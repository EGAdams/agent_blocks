"""A process that exited 0 has not thereby done what it was asked. Pure.

`JudgedExecutor` (GoF Decorator over ITaskExecutor) reads a write task's final
answer and turns "I stopped" into a failed task with a `verdict`. It can only
take success away, never grant it. The bridge's write session adds the other
half of the evidence: what actually changed, and whether the checks passed.
"""
from __future__ import annotations

import re

from .models import Envelope, TaskResult
from .ports import ITaskExecutor

WRITE_TASKS = frozenset({ "implement", "build", "repair" })

OUTCOME_RULE = """
Make the very last line of your answer exactly one of:
OUTCOME: IMPLEMENTED
OUTCOME: NO_CHANGES - <why nothing needed changing>
OUTCOME: BLOCKED - <what stopped you, and who can clear it>
Use BLOCKED whenever you stopped before meeting the request, for example because of
uncommitted changes you must not overwrite.
"""

BLOCKED   = "blocked"
UNCHANGED = "completed_without_changes"
DONE      = "implemented"

_MARKER = re.compile( r"^\W*OUTCOME:\s*(IMPLEMENTED|NO_CHANGES|BLOCKED)\b[\s:—-]*(.*?)\W*$", re.IGNORECASE )
_OPENS_BLOCKED = re.compile( r"^\W*(?:i(?:'m| am| was)\s+)?blocked\b", re.IGNORECASE )


def judge( answer: str ) -> tuple[ str, str ]:
    """( verdict, reason ). The last OUTCOME line decides; without one, an answer that opens "Blocked" is blocked."""
    lines = [ line.strip() for line in answer.splitlines() if line.strip()]
    for line in reversed( lines ):
        found = _MARKER.match( line )
        if found:
            word = found.group( 1 ).upper()
            verdict = { "IMPLEMENTED": DONE, "NO_CHANGES": UNCHANGED, "BLOCKED": BLOCKED }[ word ]
            return verdict, found.group( 2 ).strip()
    if lines and _OPENS_BLOCKED.match( lines[ 0 ]):
        return BLOCKED, lines[ 0 ]
    return DONE, ""


class JudgedExecutor:

    def __init__( self, inner: ITaskExecutor ) -> None:
        self._inner = inner

    @property
    def performer( self ) -> str:
        return self._inner.performer

    async def execute( self, envelope: Envelope ) -> TaskResult:
        result = await self._inner.execute( envelope )
        if not result.ok or envelope.task_type not in WRITE_TASKS:
            return result
        verdict, reason = judge( result.text )
        details = { **result.details, "verdict": verdict }
        if verdict == DONE:
            return result.model_copy( update={ "details": details })
        label = "BLOCKED" if verdict == BLOCKED else "COMPLETED WITHOUT CHANGES"
        head  = f"{ label }: { reason }" if reason else label
        return result.model_copy( update={ "ok": False, "text": f"{ head }\n\n{ result.text }",
                                            "details": { **details, "retryable": False }})
