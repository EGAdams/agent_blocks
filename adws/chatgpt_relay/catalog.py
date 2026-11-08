"""The relay catalog: operation name -> Operation. A new operation is one class and one line here.

Registry, the same shape as `browser_control/catalog.py`, so the ADW, the Bridge worker and the gateway
all read one table instead of each keeping a list that can drift from the others.
"""
from __future__ import annotations

from agent_comms.operations.base import IOperation, OperationSequence

from . import operations as ops
from .plane import RelayPlane, default_plane

ORDER = list(ops.ORDER)

#: "relay.ask" (the Bridge's spelling) -> the ADW operation name.
PUBLIC = {cls.name.replace("relay_", "relay."): cls.name for cls in ORDER}
PUBLIC["relay.ask"] = "relay_ask"

#: The Composite's steps, in order. Each is traced on its own, so a failed ask names the step that failed.
ASK_DESCRIPTION = ("relay.ask: send one message to a ChatGPT session and report its reply -- "
                   "ready -> guard -> snapshot -> type -> send -> settle -> answer")


def relay_operations(plane: RelayPlane | None = None) -> dict[str, IOperation]:
    """The whole table, with `relay.ask` replaced by the seven-step Composite.

    `--no-wait` skips the last two steps: the message is delivered and the run ends without waiting for a
    reply. That is the shape a session-to-session message wants -- waiting out another instance's answer
    inside this one's run is how two relays would hold each other open.
    """
    plane = plane or default_plane()
    table: dict[str, IOperation] = {cls.name: cls(plane) for cls in ORDER}
    table["relay_ask"] = OperationSequence(
        "relay_ask", ASK_DESCRIPTION,
        [ops.Ready(plane), ops.Guard(plane), ops.Snapshot(plane), ops.Type(plane), ops.Send(plane),
         ops.Settle(plane), ops.Answer(plane)],
        skip_when={"relay_settle": "no_wait", "relay_answer": "no_wait"})
    return table
