"""Registry: the public name of each notes.* operation, and the table the ADW harness runs from."""
from __future__ import annotations

from typing import Callable

from . import operations as ops

COMMANDS: dict[str, type[ops.ReviewOperation]] = {
    "notes.inspect": ops.Inspect,
    "notes.get_active_highlight": ops.ActiveHighlight,
    "notes.stop_blink": ops.StopBlink,
    "notes.activate": ops.Activate,
    "notes.activate_next": ops.ActivateNext,
    "notes.edit": ops.Edit,
    "notes.mark_resolved": ops.MarkResolved,
    "notes.verify": ops.Verify,
    "notes.rollback": ops.Rollback,
    "notes.snapshots": ops.Snapshots,
    "notes.watch": ops.Watch,
}
PUBLIC = {public: command.name for public, command in COMMANDS.items()}
READS = ("notes.inspect", "notes.get_active_highlight", "notes.verify", "notes.snapshots", "notes.watch")


def review_operations(plane_factory: Callable[[], object] | None = None) -> dict[str, ops.ReviewOperation]:
    """Each operation by its internal name, all over one plane (built lazily, so --help needs no token)."""
    from .plane import default_plane
    plane = (plane_factory or default_plane)()
    return {command.name: command(plane) for command in COMMANDS.values()}
