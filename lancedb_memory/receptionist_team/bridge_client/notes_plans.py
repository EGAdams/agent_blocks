"""One Plan per Notes operation: the model's filled form -> the exact JSON prompt, and the sentence read back.

Strategy + Registry. `NotesBuilder` (specialists.py) finds the Plan for `form.operation` in `NOTES_PLANS` and
asks it to `build()`; adding an operation is one class here and one line in `NOTES_PLANS`. The Plans mirror the
worker's Commands (`agent_bridge_worker/notes_ops/commands.py`), which re-check everything on their side.

What code decides, never the model: the diagnostic marker, every revision (remembered from the last read, see
`desk.NoteRevisions`), and the sentence that records EG's authorization for a permanent delete -- that sentence is
only sent after EG answers "yes" to a read-back that says the delete cannot be undone.
"""
import json
import re
import uuid
from typing import Protocol

NOTE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.md")
TRASH_ID = re.compile(r"\d{14}_(?:\d+_)?[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.md")
REVISION = re.compile(r"[0-9a-f]{64}")
MARKER = re.compile(r"NOTESDIAG-[0-9a-f]{12}")
COLOR = re.compile(r"[a-z]{3,10}")
MAX_ITEMS = 50


def _original(trash_id: str) -> str:
    return re.sub(r"^\d{14}_(?:\d+_)?", "", trash_id)


def words(text: str | None, limit: int = 60) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[: limit - 1] + "…"


class INotesPlan(Protocol):
    name: str
    operation: str      # the Bridge lane: "inspect" or "implement"

    def build(self, form, problem) -> tuple[dict, str]:
        """(prompt dict, read-back sentence). `problem(spoken)` raises the specialist's FormProblem."""
        ...


def _note_id(form, problem) -> str:
    note_id = (form.note_id or "").strip()
    if not NOTE_ID.fullmatch(note_id):
        problem("Which note? Ask me to list the notes, then give me the file name.")
    return note_id


def _revision(form, note_id, problem) -> str:
    revision = (form.expected_revision or "").strip().lower()
    if not REVISION.fullmatch(revision):
        problem(f"I haven't read {note_id} yet in this conversation, so I can't change it safely. "
                "Ask me to read it first.")
    return revision


class Diagnostic:
    operation = "implement"

    def __init__(self, name: str, lane: str) -> None:
        self.name, self.operation = name, lane

    def build(self, form, problem):
        req: dict = {"notes": self.name}
        if self.name == "marker_write":
            req["marker"] = f"NOTESDIAG-{uuid.uuid4().hex[:12]}"
            return req, (f"I'll write the Notes diagnostic marker {req['marker']} to the diagnostic note only, "
                         "then read it back from the store and from the page's own endpoint.")
        if self.name == "marker_clear":
            return req, "I'll remove the Notes diagnostic note. No other note is touched."
        found = MARKER.search(" ".join(filter(None, (form.marker, form.text, form.instructions))))
        if found:
            req["marker"] = found.group(0)
        return req, ""


class ListNotes:
    name, operation = "list", "inspect"

    def build(self, form, problem):
        req: dict = {"notes": "list"}
        if form.query:
            req["query"] = form.query.strip()
        if form.category:
            req["category"] = int(form.category)
        return req, ""


class SearchNotes:
    name, operation = "search", "inspect"

    def build(self, form, problem):
        if not (form.query or "").strip():
            problem("What should I search the notes for?")
        req: dict = {"notes": "search", "query": form.query.strip()}
        if form.category:
            req["category"] = int(form.category)
        return req, ""


class ReadNote:
    name, operation = "read", "inspect"

    def build(self, form, problem):
        return {"notes": "read", "id": _note_id(form, problem)}, ""


class ReadHighlights:
    name, operation = "highlights", "inspect"

    def build(self, form, problem):
        return {"notes": "highlights", "id": _note_id(form, problem)}, ""


class ListTrash:
    name, operation = "trash_list", "inspect"

    def build(self, form, problem):
        return {"notes": "trash_list"}, ""


class RefreshNote:
    name, operation = "refresh", "inspect"

    def build(self, form, problem):
        note_id = _note_id(form, problem)
        known = (form.known_revision or "").strip().lower()
        if not REVISION.fullmatch(known):
            problem(f"I haven't read {note_id} yet in this conversation, so I can't tell whether it changed. "
                    "Ask me to read it first.")
        return {"notes": "refresh", "id": note_id, "known_revision": known}, ""


class UpdateNote:
    name, operation = "update", "implement"

    def build(self, form, problem):
        note_id = _note_id(form, problem)
        revision = _revision(form, note_id, problem)
        edits = form.edits or []
        if not edits or len(edits) > MAX_ITEMS:
            problem("What exact text should change, and to what? I need the words as they appear now.")
        out, steps = [], []
        for e in edits:
            located = [x for x in (e.find, e.line, e.section, e.append) if x not in (None, "")]
            if len(located) != 1:
                problem("For each change I need either the exact old text, a line number, a section heading, or text to add at the end.")
            if e.find:
                out.append({"find": e.find, "replace": e.replace})
                steps.append(f'"{words(e.find)}" to "{words(e.replace)}"' if e.replace else f'remove "{words(e.find)}"')
            elif e.append:
                out.append({"append": e.append})
                steps.append(f'add "{words(e.append)}" at the end')
            elif e.line:
                out.append({"line": e.line, "replace": e.replace})
                steps.append(f'line {e.line} to "{words(e.replace)}"')
            else:
                out.append({"section": e.section, "replace": e.replace})
                steps.append(f'the "{words(e.section)}" section to "{words(e.replace)}"')
        more = f", and {len(steps) - 5} more" if len(steps) > 5 else ""
        said = (f"I'll change {note_id}, as I read it at revision {revision[:12]}: {'; '.join(steps[:5])}{more}. "
                "Each piece of text must match exactly once, or nothing is written.")
        return {"notes": "update", "id": note_id, "expected_revision": revision, "edits": out}, said


class ChangeHighlights:
    name, operation = "highlight", "implement"

    def build(self, form, problem):
        note_id = _note_id(form, problem)
        revision = _revision(form, note_id, problem)
        marks = form.marks or []
        if not marks or len(marks) > MAX_ITEMS:
            problem("Which text should be highlighted, in what colour, or which highlight should I clear?")
        out, steps = [], []
        for m in marks:
            action = (m.action or "").strip().lower()
            if action == "clear" and m.all:
                out.append({"action": "clear", "all": True})
                steps.append("clear every highlight")
            elif action == "clear" and m.text:
                out.append({"action": "clear", "text": m.text})
                steps.append(f'clear the highlight on "{words(m.text)}"')
            elif action in ("add", "recolor") and m.text and COLOR.fullmatch((m.color or "").lower()):
                out.append({"action": action, "text": m.text, "color": m.color.lower()})
                steps.append(f'{"highlight" if action == "add" else "recolor"} "{words(m.text)}" {m.color.lower()}')
            else:
                problem("For each highlight I need add, recolor or clear; the exact text; and a colour for add or recolor.")
        said = (f"In {note_id}, as I read it at revision {revision[:12]}, I'll {'; '.join(steps[:5])}"
                f"{', and %d more' % (len(steps) - 5) if len(steps) > 5 else ''}.")
        return {"notes": "highlight", "id": note_id, "expected_revision": revision, "marks": out}, said


class CreateNote:
    name, operation = "create", "implement"

    def build(self, form, problem):
        name = (form.title or form.new_name or "").strip()
        if not name:
            problem("What should the new note be called?")
        if not (form.text or "").strip():
            problem("What should the new note say?")
        req: dict = {"notes": "create", "name": name, "text": form.text}
        where = ""
        if form.category:
            req["category"] = int(form.category)
            where = f" in category {int(form.category)}"
        return req, f'I\'ll create a new note called "{words(name)}"{where}, {len(form.text)} characters long.'


class RenameNote:
    name, operation = "rename", "implement"

    def build(self, form, problem):
        note_id = _note_id(form, problem)
        revision = _revision(form, note_id, problem)
        if not (form.new_name or "").strip() and form.category is None:
            problem("What should the note be renamed to, or which category should it move to?")
        req: dict = {"notes": "rename", "id": note_id, "expected_revision": revision}
        parts = []
        if (form.new_name or "").strip():
            req["new_name"] = form.new_name.strip()
            parts.append(f'rename it to "{words(form.new_name)}"')
        if form.category is not None:
            req["category"] = int(form.category)
            parts.append("take it out of its category" if int(form.category) == 0
                         else f"move it to category {int(form.category)}")
        return req, f"I'll {' and '.join(parts)} for {note_id}. Its text is not touched."


class TrashNote:
    name, operation = "trash", "implement"

    def build(self, form, problem):
        note_id = _note_id(form, problem)
        revision = _revision(form, note_id, problem)
        return ({"notes": "trash", "id": note_id, "expected_revision": revision},
                f"I'll move {note_id} to the trash. It can be restored; nothing is permanently deleted.")


class RestoreNote:
    name, operation = "restore", "implement"

    def build(self, form, problem):
        trash_id = (form.trash_id or "").strip()
        if not TRASH_ID.fullmatch(trash_id):
            problem("Which trashed note? Ask me to list the trash, then tell me which one.")
        return {"notes": "restore", "trash_id": trash_id}, f"I'll restore {_original(trash_id)} from the trash."


class PurgeNote:
    name, operation = "purge", "implement"

    def build(self, form, problem):
        trash_id = (form.trash_id or "").strip()
        if not TRASH_ID.fullmatch(trash_id):
            problem("Which trashed note? Ask me to list the trash, then tell me which one.")
        req = {"notes": "purge", "trash_id": trash_id,
               "authorization": f"EG authorizes permanently deleting {trash_id}"}
        return req, (f"This permanently deletes {_original(trash_id)} from the trash. It cannot be undone, "
                     "and saying yes is your authorization.")


NOTES_PLANS: dict[str, INotesPlan] = {p.name: p for p in (
    Diagnostic("status", "inspect"), Diagnostic("marker_write", "implement"), Diagnostic("marker_clear", "implement"),
    ListNotes(), SearchNotes(), ReadNote(), ReadHighlights(), ListTrash(), RefreshNote(),
    UpdateNote(), ChangeHighlights(), CreateNote(), RenameNote(), TrashNote(), RestoreNote(), PurgeNote(),
)}
