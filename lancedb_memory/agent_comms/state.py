"""TaskLifecycle -- the one place that knows which status may follow which.

The State role, kept as a transition table rather than a class per state:
every state here has the same behaviour (be stored in one mailbox box), and
only the allowed moves differ. A move not in the table raises, so a worker bug
cannot, say, complete a task nobody claimed or resurrect a cancelled one.
"""
from __future__ import annotations

from .models import Envelope, TaskStatus, utc_now


class IllegalTransition( RuntimeError ):
    pass


_ALLOWED: dict[ TaskStatus, frozenset[ TaskStatus ]] = {
    TaskStatus.QUEUED:    frozenset({ TaskStatus.RUNNING, TaskStatus.CANCELLED }),
    TaskStatus.RUNNING:   frozenset({ TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.QUEUED }),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED:    frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.TIMED_OUT: frozenset(),
}

# Which mailbox box holds a task in each status. Swarm-Forge's rule: queue
# state is represented by file location.
BOX_FOR: dict[ TaskStatus, str ] = {
    TaskStatus.QUEUED:    "new",
    TaskStatus.RUNNING:   "in_process",
    TaskStatus.SUCCEEDED: "completed",
    TaskStatus.FAILED:    "failed",
    TaskStatus.CANCELLED: "cancelled",
}
BOXES = tuple( BOX_FOR.values())


def advance( envelope: Envelope, to: TaskStatus ) -> Envelope:
    """A copy of `envelope` in status `to`, with its audit timestamp stamped."""
    if to not in _ALLOWED[ envelope.status ]:
        raise IllegalTransition( f"task { envelope.id }: { envelope.status.value } -> { to.value } is not allowed" )
    stamp: dict[ str, object ] = { "status": to }
    if to is TaskStatus.RUNNING:
        stamp[ "dequeued_at" ] = utc_now()
        stamp[ "attempts" ]    = envelope.attempts + 1
    elif to in ( TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED ):
        stamp[ "completed_at" ] = utc_now()
    elif to is TaskStatus.QUEUED:
        stamp[ "enqueued_at" ] = utc_now()
    return envelope.model_copy( update=stamp )
