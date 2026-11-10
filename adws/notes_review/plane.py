"""ReviewPlane: the Facade every operation works through, and `default_plane()`, the one Abstract Factory.

`default_plane()` is the only place in notes_review that names concrete classes. Tests build a plane from
fakes; nothing else changes.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .guards import default_chain
from .interfaces import IAttachmentStore, IClock, INoteStore, IPageReader, IReviewGuard, IReviewObserver, ISnapshotStore

ADWS = Path(__file__).resolve().parent.parent
DATA = ADWS / "adw_data" / "notes_review"


class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ReviewPlane:
    def __init__(self, store: INoteStore, snapshots: ISnapshotStore, page: IPageReader, guards: IReviewGuard,
                 observer: IReviewObserver, clock: IClock, attachments: IAttachmentStore | None = None) -> None:
        self.store = store
        self.snapshots = snapshots
        self.page = page
        self.guards = guards
        self.observer = observer
        self.clock = clock
        self.attachments = attachments


def default_plane() -> ReviewPlane:
    from .observers import JsonlActivity
    from .page import AdwPageReader
    from .snapshots import FileSnapshotStore
    from .store import BridgeAttachmentStore, bridge_from_environment
    uv = os.environ.get("UV_BIN") or str(Path.home() / ".local" / "bin" / "uv")
    store = bridge_from_environment()
    return ReviewPlane(store=store, attachments=BridgeAttachmentStore(store), snapshots=FileSnapshotStore(DATA / "snapshots"),
                       page=AdwPageReader(uv, ADWS / "adw_browser_control.py", ADWS.parent),
                       guards=default_chain(), observer=JsonlActivity(DATA / "activity.jsonl"), clock=SystemClock())
