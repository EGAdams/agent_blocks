"""IHandoffBoxes on disk, in Swarm-Forge's layout. A message's state is the box its file sits in.

    handoffs/outbox/tmp   written here first, then renamed, so no reader ever sees half a file
    handoffs/outbox       waiting for the carrier        -> sent | failed
    handoffs/inbox/new    arrived, nobody working on it  -> in_process -> completed | failed
                          in_process -> new again when the agent's usage limit was used up (release),
                          and failed -> new for one todo an operator names (requeue)
"""
from __future__ import annotations

import os
from pathlib import Path

from agent_comms.models import Envelope, TaskStatus, utc_now
from agent_comms.state import advance

from ..envelopes import BOXES, ID_PATTERN, INBOX, filename
from ..errors import Refused
from .stores import write_atomic


class FileHandoffBoxes:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._folders = {"new": root / "inbox" / "new", "in_process": root / "inbox" / "in_process",
                         "completed": root / "inbox" / "completed", "failed": root / "inbox" / "failed",
                         "outbox": root / "outbox", "sent": root / "sent", "undelivered": root / "failed"}

    def post(self, envelope: Envelope) -> None:
        tmp = self._root / "outbox" / "tmp" / filename(envelope)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self._folders["outbox"] / filename(envelope))

    def sent(self, envelope: Envelope) -> None:
        self._move(envelope, "outbox", "sent", envelope)

    def undelivered(self, envelope: Envelope, why: str) -> None:
        self._move(envelope, "outbox", "undelivered", envelope.model_copy(update={"error": why[:500]}))

    def receive(self, envelope: Envelope) -> Envelope:
        if not ID_PATTERN.match(envelope.id):
            raise Refused(f"not a handoff id: {envelope.id[:80]}")
        if any(self._where(envelope.id, box) for box in INBOX):
            raise Refused(f"handoff {envelope.id} was already received")
        write_atomic(self._folders["new"] / filename(envelope), envelope.model_dump_json(indent=2))
        return envelope

    def claim(self, envelope_id: str) -> Envelope | None:
        waiting = self._where(envelope_id, "new")
        if waiting is None:
            return None
        taken = self._folders["in_process"] / waiting.name
        taken.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(waiting, taken)                    # the atomic step: only one claimer wins
        except FileNotFoundError:
            return None
        running = advance(Envelope.model_validate_json(taken.read_text(encoding="utf-8")), TaskStatus.RUNNING)
        write_atomic(taken, running.model_dump_json(indent=2))
        return running

    def release(self, envelope: Envelope) -> Envelope:
        waiting = advance(envelope, TaskStatus.QUEUED)
        self._move(envelope, "in_process", "new", waiting)
        return waiting

    def requeue(self, envelope_id: str, why: str) -> Envelope | None:
        """The one way out of inbox/failed, which agent_comms' lifecycle treats as final: same id, same file name,
        back in inbox/new, with the failure it had kept in its context. Written before the failed file is removed."""
        failed = self._where(envelope_id, "failed")
        if failed is None:
            return None
        old = Envelope.model_validate_json(failed.read_text(encoding="utf-8"))
        waiting = old.model_copy(update={
            "status": TaskStatus.QUEUED, "enqueued_at": utc_now(), "completed_at": None, "result": None,
            "error": None, "context": {**old.context, "requeued": why[:300], "previous_error": (old.error or "")[:500]}})
        write_atomic(self._folders["new"] / failed.name, waiting.model_dump_json(indent=2))
        failed.unlink(missing_ok=True)
        return waiting

    def settle(self, envelope: Envelope, ok: bool) -> Envelope:
        done = advance(envelope, TaskStatus.SUCCEEDED if ok else TaskStatus.FAILED)
        self._move(envelope, "in_process", "completed" if ok else "failed", done)
        return done

    def box(self, name: str) -> list[Envelope]:
        found = []
        folder = self._folders[name]
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                found.append(Envelope.model_validate_json(path.read_text(encoding="utf-8")))
            except (ValueError, FileNotFoundError):
                continue                                 # moved while we looked, or not a handoff
        return found

    def find(self, envelope_id: str) -> tuple[str, Envelope] | None:
        if not ID_PATTERN.match(envelope_id):
            return None
        for name in BOXES:
            path = self._where(envelope_id, name)
            if path is not None:
                try:
                    return name, Envelope.model_validate_json(path.read_text(encoding="utf-8"))
                except (ValueError, FileNotFoundError):
                    continue
        return None

    def _where(self, envelope_id: str, box: str) -> Path | None:
        if not ID_PATTERN.match(envelope_id):
            return None
        return next(iter(sorted(self._folders[box].glob(f"*_{envelope_id}_to_*.json"))), None)

    def _move(self, envelope: Envelope, source: str, target: str, content: Envelope) -> None:
        write_atomic(self._folders[target] / filename(envelope), content.model_dump_json(indent=2))
        (self._folders[source] / filename(envelope)).unlink(missing_ok=True)
