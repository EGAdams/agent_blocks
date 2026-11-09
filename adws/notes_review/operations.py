"""The notes.* operations: one Command each, over the `ReviewPlane` Facade.

Reads (`inspect`, `get_active_highlight`, `verify`, `watch`) never write. Writes share one Template Method,
`WriteOperation.perform`:

    read the note and its revision -> `change()` (the subclass's one decision) -> every guard
    -> snapshot the note as it was (Memento) -> write over the revision just read -> read it back
    -> verify the saved text (and, when asked, the reloaded page) -> answer

so no write can skip a guard, a snapshot, the revision check or the read-back. A refusal writes nothing and
says why. Every answer carries the same fields: note, revision, active item and text, highlight status,
pending items, and (stamped by the ADW harness) the ADW id.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from agent_comms.operations.base import OperationOutcome

from browser_control.highlights import normalize

from .document import DocumentRefusal, ReviewDocument
from .interfaces import NoteMoved
from .models import NoteSnapshot, ProposedChange, ReviewItem, Verification

NOTE_NAME = re.compile(r"^[A-Za-z0-9][\w.\-() ]{0,160}\.md$")
WATCH_UNTIL = ("clear", "change", "active_change")


def ok(line: str, **data: Any) -> OperationOutcome:
    return OperationOutcome(ok=True, line=line, data=data)


def refused(line: str, cause: str, **data: Any) -> OperationOutcome:
    return OperationOutcome(ok=False, line=line, data={"cause": cause, **data})


def status_of(doc: ReviewDocument) -> str:
    if doc.active():
        return "blinking"
    if doc.explicit and not doc.focus:
        return "stopped"
    if doc.explicit and not doc.focus_unique:
        return "focus_not_unique"
    if doc.explicit:
        return "focus_not_found"
    return "none"


def report(doc: ReviewDocument, revision: str) -> dict[str, Any]:
    """The fields every answer carries."""
    active = doc.active()
    return {"note": doc.filename, "revision": revision, "highlight_status": status_of(doc),
            "active_item_id": active.id if active else None, "active_text": active.target if active else None,
            "active_line": active.line if active else None, "focus": doc.focus,
            "pending": [_brief(i) for i in doc.pending()], "pending_count": len(doc.pending())}


def _brief(item: ReviewItem) -> dict[str, Any]:
    return {"id": item.id, "line": item.line, "kind": item.kind, "text": item.target, "yellow": item.yellow,
            "active": item.active, "activatable": item.activatable, "why_not": item.why_not}


def _quote(text: str | None, limit: int = 160) -> str:
    text = text or ""
    return '"' + (text if len(text) <= limit else text[:limit - 3] + "...") + '"'


def page_agrees(target: str | None, blinking: list[str]) -> bool:
    """The page blinks the saved target (nothing, when the target is None), compared as rendered text."""
    if not target:
        return not blinking
    want = normalize(target)
    return len(blinking) == 1 and bool(want) and (want in normalize(blinking[0]) or normalize(blinking[0]) in want)


class ReviewOperation:
    """Template Method: argument checks and the failures every operation answers the same way."""
    name = ""
    description = ""
    performer = "notes_review"

    def __init__(self, plane) -> None:
        self._p = plane

    def run(self, args: dict[str, Any]) -> OperationOutcome:
        note = str(args.get("note") or "").strip()
        if not NOTE_NAME.fullmatch(note):
            return refused("Name the note by its file name, e.g. 2026-11-03_draft-....md.", "no_note")
        try:
            return self.perform(note, args)
        except NoteMoved as exc:
            return refused(f"{note} changed while this ran ({exc}); nothing was written. Read it again.",
                           "note_moved", note=note)
        except FileNotFoundError:
            return refused(f"There is no note called {note}.", "note_not_found", note=note)
        except DocumentRefusal as exc:
            return refused(f"Nothing was written: {exc}.", "refused", note=note)
        except (OSError, RuntimeError) as exc:
            return refused(f"The Notes interface could not be used: {type(exc).__name__}: {exc}",
                           "notes_unreachable", note=note)

    def perform(self, note: str, args: dict[str, Any]) -> OperationOutcome:
        raise NotImplementedError

    def _doc(self, note: str) -> tuple[ReviewDocument, str]:
        text, revision = self._p.store.read(note)
        return ReviewDocument(note, text), revision

    def _item(self, doc: ReviewDocument, args: dict[str, Any]) -> ReviewItem | OperationOutcome:
        wanted = str(args.get("item_id") or "").strip()
        if not wanted:
            return refused("Say which item (item_id, from notes.inspect).", "no_item", **report(doc, doc.revision))
        found = doc.item(wanted)
        if found is None:
            return refused(f"{doc.filename} has no review item {wanted} now; its words may have changed. "
                           "Inspect it again for the current ids.", "item_not_found", **report(doc, doc.revision))
        return found

    def _verify(self, note: str, expected_text: str | None, page: bool) -> Verification:
        """Read the saved note back (and, when asked, the reloaded page) and compare with what was meant."""
        saved_text, saved_revision = self._p.store.read(note)
        saved = ReviewDocument(note, saved_text)
        expected = ReviewDocument(note, expected_text) if expected_text is not None else saved
        problems = []
        if expected_text is not None and saved_text != expected_text:
            problems.append("the saved note is not what was written")
        want = expected.active().target if expected.active() else None
        have = saved.active().target if saved.active() else None
        if want != have:
            problems.append("the saved note would blink a different item")
        result = Verification(saved=expected_text is None or saved_text == expected_text,
                              saved_revision=saved_revision, expected_active=want, saved_active=have)
        if page:
            reading = self._p.page.read(note, True)
            result.page_checked = reading.ok
            result.page_adw_id = reading.adw_id
            if not reading.ok:
                problems.append(f"the page could not be read: {reading.line}")
            else:
                result.page_blinking = reading.blinking
                result.page_text_visible = reading.text_visible
                result.page_revision_matches = (reading.revision == saved_revision) if reading.revision else None
                result.page_agrees = page_agrees(have, reading.blinking)
                if result.page_revision_matches is False:
                    problems.append("the reloaded page shows a different revision than the saved note")
                if not result.page_agrees:
                    problems.append(f"the reloaded page blinks {reading.blinking or 'nothing'}, the saved note "
                                    f"says {_quote(have) if have else 'nothing'}")
                if reading.blinking and not reading.text_visible:
                    problems.append("the page hides the blinking item's text for part of the blink")
        result.problems = problems
        return result


# -- reads ---------------------------------------------------------------------------------------------


class Inspect(ReviewOperation):
    name = "notes_inspect"
    description = "notes.inspect: a note's review items, which one blinks, and its revision (read only)"

    def perform(self, note, args):
        doc, revision = self._doc(note)
        active = doc.active()
        said = (f"{note}: {len(doc.pending())} review item{'s' if len(doc.pending()) != 1 else ''} open. "
                + (f"Blinking: line {active.line}, {_quote(active.target)}." if active else
                   f"Nothing blinks ({status_of(doc).replace('_', ' ')})."))
        return ok(said, **report(doc, revision), items=[i.model_dump() for i in doc.items], deed=doc.deed)


class ActiveHighlight(ReviewOperation):
    name = "notes_get_active_highlight"
    description = ("notes.get_active_highlight: the item that blinks, word for word, from the saved note "
                   "and (with page) from the reloaded dashboard page")

    def perform(self, note, args):
        doc, revision = self._doc(note)
        active = doc.active()
        data = report(doc, revision)
        said = (f"In {note} the blinking item is line {active.line}: {_quote(active.target, 600)}"
                if active else f"Nothing in {note} is blinking ({status_of(doc).replace('_', ' ')}).")
        if args.get("page"):
            check = self._verify(note, None, True)
            data["verification"] = check.model_dump()
            said += (" The reloaded dashboard page agrees, and the text stays visible." if check.ok else
                     " Page check: " + "; ".join(check.problems) + ".")
            return OperationOutcome(ok=check.ok, line=said, data={**data, "cause": "" if check.ok else "page_disagrees"})
        return ok(said, **data)


class Verify(ReviewOperation):
    name = "notes_verify"
    description = ("notes.verify: re-read the saved note and reload the dashboard page, and say whether they "
                   "agree on the revision and the blinking item (and, with expect_item_id, that it is that one)")

    def perform(self, note, args):
        doc, revision = self._doc(note)
        check = self._verify(note, None, not args.get("no_page"))
        wanted = str(args.get("expect_item_id") or "").strip()
        active = doc.active()
        if wanted and (active is None or active.id != wanted):
            check.problems.append(f"the blinking item is {active.id if active else 'none'}, not {wanted}")
        said = (f"Verified {note} at revision {check.saved_revision[:12]}: "
                + (f"line {active.line} blinks" if active else "nothing blinks")
                + (" on the saved note and on the reloaded page." if check.page_checked else " (saved note only).")
                if check.ok else f"{note} did not verify: " + "; ".join(check.problems) + ".")
        return OperationOutcome(ok=check.ok, line=said, data={**report(doc, revision), "verification":
                                check.model_dump(), "cause": "" if check.ok else "not_verified"})


class Watch(ReviewOperation):
    """The watcher (successor of adw_notes_highlight_watch.py): polls the saved note until something happens."""
    name = "notes_watch"
    description = ("notes.watch: wait until an item stops being highlighted (until=clear), the note changes "
                   "(change) or a different item blinks (active_change); polls the saved note, read only")

    def perform(self, note, args):
        until = str(args.get("until") or "clear")
        if until not in WATCH_UNTIL:
            return refused(f"until is one of {', '.join(WATCH_UNTIL)}.", "bad_until")
        timeout = min(max(float(args.get("timeout") or 60), 1.0), 3600.0)
        interval = min(max(float(args.get("interval") or 3), 0.5), 60.0)
        doc, revision = self._doc(note)
        start_active = doc.active()
        watched = doc.item(str(args.get("item_id") or "")) if args.get("item_id") else start_active
        if until == "clear" and watched is None:
            return refused(f"Nothing to watch in {note}: name an item_id, or one must be blinking.", "no_item",
                           **report(doc, revision))
        deadline = self._p.clock.monotonic() + timeout
        polls = 0
        while True:
            polls += 1
            current, current_revision = self._doc(note)
            now = current.active()
            met = (until == "change" and current_revision != revision or
                   until == "active_change" and (now.id if now else None) != (start_active.id if start_active else None) or
                   until == "clear" and not any(i.target == watched.target and (i.yellow or i.open) for i in current.items))
            if met or self._p.clock.monotonic() >= deadline:
                break
            self._p.clock.sleep(interval)
        data = {**report(current, current_revision), "until": until, "met": met, "polls": polls,
                "watched_item_id": watched.id if watched else None, "start_revision": revision}
        if met and args.get("page"):
            data["verification"] = self._verify(note, None, True).model_dump()
        what = {"clear": f"{_quote(watched.target if watched else '')} is no longer highlighted",
                "change": "the note changed", "active_change": "a different item blinks"}[until]
        if met:
            return ok(f"In {note}, {what} (after {polls} read{'s' if polls != 1 else ''}).", **data)
        return refused(f"Waited {int(timeout)}s: in {note}, {what.replace('is no longer', 'is still').replace('changed', 'did not change').replace('a different item blinks', 'the same item still blinks')}.",
                       "timeout", **data)


# -- writes --------------------------------------------------------------------------------------------


class WriteOperation(ReviewOperation):
    """The write Template Method. Subclasses answer one question in `change()`: what the note should become."""

    def change(self, doc: ReviewDocument, args: dict[str, Any]) -> tuple[ReviewItem | None, str, str] | OperationOutcome:
        """(the item it is about, the new text, one sentence saying what was done) -- or a refusal."""
        raise NotImplementedError

    def perform(self, note, args):
        doc, revision = self._doc(note)
        planned = self.change(doc, args)
        if isinstance(planned, OperationOutcome):
            return planned
        item, after, done = planned
        if after == doc.text:
            return ok(f"{note} already reads that way; nothing was written.", **report(doc, revision), changed=False)
        new_doc = ReviewDocument(note, after)
        new_item = next((i for i in new_doc.items if item and i.line == item.line), None)
        proposal = ProposedChange(operation=self.op, note=note, before=doc.text, after=after, item=item,
                                  new_item=new_item, evidence=str(args.get("evidence") or ""),
                                  approval=str(args.get("approval") or ""),
                                  expected_revision=str(args.get("expected_revision") or ""),
                                  current_revision=revision, allowed_lines=self._allowed(doc, item))
        objection = self._p.guards.objection(proposal)
        if objection:
            return refused(f"Nothing was written: {objection}.", "guard", **report(doc, revision))
        snapshot = NoteSnapshot(id="s-" + uuid.uuid4().hex[:10], note=note, operation=self.op,
                                adw_id=str(args.get("adw_id") or ""), taken_at=self._p.clock.now_iso(),
                                before=doc.text, before_revision=revision, item_id=item.id if item else "",
                                reason=proposal.evidence or proposal.approval)
        self._p.snapshots.save(snapshot)
        new_revision = self._p.store.write(note, after, revision)
        snapshot.after_revision = new_revision
        self._p.snapshots.save(snapshot)
        check = self._verify(note, after, bool(args.get("page")))
        self._p.observer.notify(self.op, {"note": note, "item_id": snapshot.item_id, "snapshot_id": snapshot.id,
                                          "before_revision": revision, "after_revision": new_revision,
                                          "adw_id": snapshot.adw_id, "grounds": snapshot.reason,
                                          "verified": check.ok})
        said = f"{done} Saved {note} (revision {new_revision[:12]}); undo with snapshot {snapshot.id}."
        if not check.ok:
            said += " But verification failed: " + "; ".join(check.problems) + "."
        extra = {"new_item_id": new_item.id if new_item else None} if item else {}
        return OperationOutcome(ok=check.ok, line=said, data={
            **report(new_doc, new_revision), **extra, "changed": True, "item_id": item.id if item else None,
            "snapshot_id": snapshot.id, "previous_revision": revision, "verification": check.model_dump(),
            "cause": "" if check.ok else "not_verified"})

    @property
    def op(self) -> str:
        return self.name.removeprefix("notes_")

    @staticmethod
    def _allowed(doc: ReviewDocument, item: ReviewItem | None) -> list[int]:
        lines = [item.line] if item else []
        marker = next((n for n, raw in enumerate(doc.raw, 1) if raw.lstrip().startswith("<!--")
                       and "notes-review-focus" in raw), None)
        if marker:
            lines.append(marker)
        else:
            lines.append(len(doc.raw) + 1)
            if doc.lines and not doc.lines[-1].endswith(("\n", "\r")):
                lines.append(len(doc.raw))
        return lines


class StopBlink(WriteOperation):
    name = "notes_stop_blink"
    description = "notes.stop_blink: stop the blinking without deleting or unhighlighting any text"

    def change(self, doc, args):
        active = doc.active()
        return active, doc.with_focus(""), (f"Stopped the blinking of line {active.line}; its words and "
                                            "highlight are untouched." if active else "Nothing blinks now.")


class Activate(WriteOperation):
    name = "notes_activate"
    description = "notes.activate: make one item blink (yellow background, text stays visible)"

    def change(self, doc, args):
        item = self._item(doc, args)
        if isinstance(item, OperationOutcome):
            return item
        if not item.activatable:
            return refused(f"Line {item.line} cannot blink: {item.why_not}.", "not_activatable",
                           **report(doc, doc.revision))
        return item, doc.with_focus(item.target), f"Line {item.line} now blinks: {_quote(item.target)}."


class ActivateNext(WriteOperation):
    name = "notes_activate_next"
    description = "notes.activate_next: make the next unresolved item after the blinking one (or after item_id) blink"

    def change(self, doc, args):
        after = doc.item(str(args.get("item_id") or "")) if args.get("item_id") else doc.active()
        upcoming = doc.next_after(after)
        if upcoming is None:
            return refused(f"No unresolved item that can blink comes after line {after.line if after else 0} "
                           f"of {doc.filename}.", "none_left", **report(doc, doc.revision))
        return upcoming, doc.with_focus(upcoming.target), (f"Line {upcoming.line} now blinks: "
                                                           f"{_quote(upcoming.target)}.")


class Edit(WriteOperation):
    name = "notes_edit"
    description = ("notes.edit: replace one item's words (its highlight and the rest of the note untouched); "
                   "an edit may not resolve an open item")

    def change(self, doc, args):
        item = self._item(doc, args)
        if isinstance(item, OperationOutcome):
            return item
        replace = str(args.get("replace") or "").strip()
        if not replace:
            return refused("Give the new words in replace.", "no_text", **report(doc, doc.revision))
        return item, doc.with_item_text(item, replace), f"Line {item.line} now reads {_quote(replace)}."


class MarkResolved(WriteOperation):
    name = "notes_mark_resolved"
    description = ("notes.mark_resolved: remove an item's highlight (optionally with resolved wording in replace), "
                   "only with evidence or EG's approval; advance makes the next item blink in the same save")

    def change(self, doc, args):
        item = self._item(doc, args)
        if isinstance(item, OperationOutcome):
            return item
        replace = str(args.get("replace") or "").strip()
        if not item.yellow and not replace:
            return refused(f"Line {item.line} is open because of its own words, not a highlight; give the "
                           "resolved wording in replace.", "needs_replacement", **report(doc, doc.revision))
        text = doc.with_item_text(item, replace) if replace else doc.text
        middle = ReviewDocument(doc.filename, text)
        here = next((i for i in middle.items if i.line == item.line), None)
        if here is not None and here.yellow:
            text = middle.without_highlight(here)
        done = f"Marked line {item.line} resolved."
        if args.get("advance"):
            final = ReviewDocument(doc.filename, text)
            upcoming = final.next_after(item)
            if upcoming is not None:
                text = final.with_focus(upcoming.target)
                done += f" Line {upcoming.line} now blinks: {_quote(upcoming.target)}."
            else:
                text = final.with_focus("") if final.explicit else text
                done += " No unresolved item is left after it."
        return item, text, done


class Rollback(ReviewOperation):
    """Undo: write back what a snapshot holds, only over the revision our own writes left."""
    name = "notes_rollback"
    description = ("notes.rollback: put a note back as it was before a snapshot's write -- only if nobody has "
                   "changed it since our own writes (chain=true undoes every later write of ours too)")

    def perform(self, note, args):
        wanted = self._p.snapshots.get(str(args.get("snapshot_id") or ""))
        if wanted is None or wanted.note != note:
            return refused(f"There is no snapshot {args.get('snapshot_id')!r} of {note}.", "no_snapshot")
        doc, revision = self._doc(note)
        expected = str(args.get("expected_revision") or "")
        if expected and expected != revision:
            return refused(f"{note} changed since revision {expected[:12]}; nothing was written.", "note_moved",
                           **report(doc, revision))
        newer = [s for s in self._p.snapshots.for_note(note) if s.taken_at > wanted.taken_at and s.after_revision]
        chain = [wanted] + sorted(newer, key=lambda s: s.taken_at)
        if not args.get("chain"):
            chain = [wanted]
        links = all(a.after_revision == b.before_revision for a, b in zip(chain, chain[1:]))
        if not links or chain[-1].after_revision != revision:
            return refused(f"{note} has been changed since that write (it is at {revision[:12]}, the write left "
                           f"{chain[-1].after_revision[:12]}); an undo would overwrite newer work, so nothing was "
                           "written." + ("" if args.get("chain") else " chain=true undoes our later writes too."),
                           "newer_edits", **report(doc, revision))
        if doc.text == wanted.before:
            return ok(f"{note} already reads as it did before {wanted.id}.", **report(doc, revision), changed=False)
        undo = NoteSnapshot(id="s-" + uuid.uuid4().hex[:10], note=note, operation="rollback",
                            adw_id=str(args.get("adw_id") or ""), taken_at=self._p.clock.now_iso(),
                            before=doc.text, before_revision=revision, reason=f"undo of {wanted.id}")
        self._p.snapshots.save(undo)
        new_revision = self._p.store.write(note, wanted.before, revision)
        undo.after_revision = new_revision
        self._p.snapshots.save(undo)
        check = self._verify(note, wanted.before, bool(args.get("page")))
        restored = new_revision == wanted.before_revision
        self._p.observer.notify("rollback", {"note": note, "snapshot_id": wanted.id, "undo_snapshot_id": undo.id,
                                             "after_revision": new_revision, "adw_id": undo.adw_id})
        said = (f"Put {note} back as it was before {wanted.id} (revision {new_revision[:12]}"
                + (", byte for byte" if restored else "") + f"). This undo is itself snapshot {undo.id}.")
        if not check.ok:
            said += " But verification failed: " + "; ".join(check.problems) + "."
        return OperationOutcome(ok=check.ok and restored, line=said, data={
            **report(ReviewDocument(note, wanted.before), new_revision), "changed": True, "snapshot_id": undo.id,
            "undid": [s.id for s in chain], "restored_revision": wanted.before_revision,
            "byte_for_byte": restored, "verification": check.model_dump(),
            "cause": "" if check.ok and restored else "not_verified"})


class Snapshots(ReviewOperation):
    name = "notes_snapshots"
    description = "notes.snapshots: the undo points kept for a note, newest first (read only)"

    def perform(self, note, args):
        found = self._p.snapshots.for_note(note)[:int(args.get("limit") or 20)]
        rows = [{"id": s.id, "operation": s.operation, "taken_at": s.taken_at, "item_id": s.item_id,
                 "before_revision": s.before_revision, "after_revision": s.after_revision, "adw_id": s.adw_id,
                 "reason": s.reason} for s in found]
        return ok(f"{len(rows)} undo point{'s' if len(rows) != 1 else ''} kept for {note}.", note=note,
                  snapshots=rows)
