"""The notes.* PDF attachment operations: one Command each, over the `ReviewPlane` Facade's `attachments`.

A PDF is kept by the dashboard beside the notes (`~/notes/.attachments/`) and listed under the note. It is not
written into the note, so attaching or detaching one never changes the note's words, its blinking item or its
review status. `AttachmentChange.perform` is a Template Method that checks this every time:

    read the note's revision -> `change()` (the subclass's one call) -> read the revision again
    -> list the note's attachments -> `confirm()` (the subclass checks the list says what it meant) -> answer

An answer is `verified` only when the revision did not move and the list confirms the change. Reads
(`attachments`, `attachment`) never change anything. Every attachment in an answer carries the dashboard links
that open it (`view_url`), save it (`download_url`), and the editor page where its Print button is.
"""
from __future__ import annotations

import hashlib
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from agent_comms.operations.base import OperationOutcome

from .interfaces import AttachmentMissing
from .operations import ReviewOperation, ok, refused, report

MAX_BYTES = 10 * 1024 * 1024
ATTACHMENT_ID = re.compile(r"^a-[0-9a-f]{12}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_DASHBOARD = "https://desktop-2obsqmc.tailb8fc54.ts.net"


def dashboard_url() -> str:
    """Where people open the dashboard (NOTES_DASHBOARD_URL overrides it)."""
    return os.environ.get("NOTES_DASHBOARD_URL", DEFAULT_DASHBOARD).rstrip("/")


def with_links(note: str, entry: dict) -> dict:
    """The entry plus the dashboard links that open, save and print it."""
    base = dashboard_url()
    query = urllib.parse.urlencode({"filename": note, "id": entry.get("id", "")})
    return {**entry, "view_url": f"{base}/api/notes/attachment-file?{query}",
            "download_url": f"{base}/api/notes/attachment-file?{query}&download=1",
            "print": "Open the note in the dashboard's Notes editor and press Print beside this attachment, "
                     "or open view_url and use the PDF viewer's Print."}


def _attachment_id(args: dict[str, Any]) -> str | OperationOutcome:
    wanted = str(args.get("attachment_id") or "").strip()
    if not ATTACHMENT_ID.fullmatch(wanted):
        return refused("Say which attachment (attachment_id, a- and 12 hex, from notes.attachments).",
                       "no_attachment")
    return wanted


class AttachmentOperation(ReviewOperation):
    """Every attachment operation needs the attachment store; a plane without one refuses honestly."""

    def run(self, args: dict[str, Any]) -> OperationOutcome:
        if self._p.attachments is None:
            return refused("PDF attachments are not wired on this machine; nothing was done.", "no_attachments")
        try:
            return super().run(args)
        except AttachmentMissing as exc:
            note = str(args.get("note") or "")
            return refused(f"{note} has no attachment {exc}; list them with notes.attachments.",
                           "attachment_not_found", note=note)


# -- reads ---------------------------------------------------------------------------------------------


class Attachments(AttachmentOperation):
    name = "notes_attachments"
    description = "notes.attachments: the PDFs attached to a note, with View / Download links (read only)"

    def perform(self, note, args):
        text, revision = self._p.store.read(note)
        rows = [with_links(note, a) for a in self._p.attachments.list(note)]
        said = (f"{note}: {len(rows)} PDF{'s' if len(rows) != 1 else ''} attached"
                + (": " + "; ".join(f"{a.get('title') or a.get('filename')} ({a['id']})" for a in rows) if rows
                   else ".") )
        return ok(said, note=note, revision=revision, attachments=rows)


class AttachmentInfo(AttachmentOperation):
    name = "notes_attachment"
    description = "notes.attachment: one attached PDF, re-hashed on disk to confirm it is intact (read only)"

    def perform(self, note, args):
        wanted = _attachment_id(args)
        if isinstance(wanted, OperationOutcome):
            return wanted
        entry = with_links(note, self._p.attachments.get(note, wanted))
        if not entry.get("intact"):
            return refused(f"{wanted} on {note} no longer matches its sha256 on disk; attach the PDF again.",
                           "attachment_damaged", note=note, attachment=entry)
        return ok(f"{entry.get('title') or entry.get('filename')} ({wanted}) is attached to {note} and intact: "
                  f"{entry.get('bytes')} bytes, {entry.get('pages') or '?'} pages, sha256 {str(entry.get('sha256'))[:12]}.",
                  note=note, attachment=entry)


# -- changes -------------------------------------------------------------------------------------------


class AttachmentChange(AttachmentOperation):
    """Template Method: a change to a note's attachments that must leave the note itself exactly as it was."""

    def perform(self, note, args):
        prepared = self.prepare(note, args)
        if isinstance(prepared, OperationOutcome):
            return prepared
        _, before = self._p.store.read(note)
        result = self.change(note, prepared)
        text, after = self._p.store.read(note)
        listed = self._p.attachments.list(note)
        problems = self.confirm(result, listed)
        unchanged = before == after
        if not unchanged:
            problems.append(f"the note's revision moved from {before[:12]} to {after[:12]} while this ran")
        from .document import ReviewDocument
        data = {**report(ReviewDocument(note, text), after), "note_unchanged": unchanged,
                "revision_before": before, "verified": not problems, "problems": problems,
                "attachments": [with_links(note, a) for a in listed], **self.details(note, result)}
        self._p.observer.notify(self.op, {"note": note, "attachment_id": result.get("id"), "verified": not problems,
                                          "adw_id": args.get("adw_id"), "at": self._p.clock.now_iso()})
        if problems:
            return refused(f"{self.done(note, result)}, but it could not be confirmed: " + "; ".join(problems) + ".",
                           "not_verified", **data)
        return ok(f"{self.done(note, result)}. The note's text is unchanged (revision {after[:12]}).", **data)

    op = ""

    def prepare(self, note: str, args: dict[str, Any]) -> Any:
        raise NotImplementedError

    def change(self, note: str, prepared: Any) -> dict:
        raise NotImplementedError

    def confirm(self, result: dict, listed: list[dict]) -> list[str]:
        raise NotImplementedError

    def details(self, note: str, result: dict) -> dict:
        return {}

    def done(self, note: str, result: dict) -> str:
        raise NotImplementedError


class AttachPdf(AttachmentChange):
    name = "notes_attach_pdf"
    description = "notes.attach_pdf: attach a PDF file to a note (--pdf-file, --title); the note's text is untouched"
    op = "attach_pdf"

    def prepare(self, note, args):
        path = Path(str(args.get("pdf_file") or ""))
        if not str(args.get("pdf_file") or "") or not path.is_file():
            return refused("Name the PDF to attach (--pdf-file, a file on this machine).", "no_pdf", note=note)
        if path.stat().st_size > MAX_BYTES:
            return refused(f"That PDF is over {MAX_BYTES // (1024 * 1024)} MiB; nothing was attached.", "too_large",
                           note=note)
        data = path.read_bytes()
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
            return refused("That file is not a whole PDF (no %PDF- header or no %%EOF at its end); nothing was "
                           "attached.", "not_a_pdf", note=note)
        sha = hashlib.sha256(data).hexdigest()
        declared = str(args.get("upload_sha256") or "").strip().lower()
        if declared and (not SHA256.fullmatch(declared) or declared != sha):
            return refused(f"That PDF hashes to {sha[:12]}, not the {declared[:12]} it was sent as; nothing was "
                           "attached.", "hash_mismatch", note=note)
        title = str(args.get("title") or "").strip()[:120]
        name = str(args.get("name") or path.name).strip()[:160]
        return {"data": data, "sha256": sha, "title": title, "name": name}

    def change(self, note, prepared):
        entry, created = self._p.attachments.attach(note, prepared["data"], prepared["title"], prepared["name"],
                                                    prepared["sha256"])
        return {**entry, "created": created, "expected_sha256": prepared["sha256"]}

    def confirm(self, result, listed):
        mine = [a for a in listed if a.get("id") == result.get("id")]
        if not mine:
            return ["the note's attachment list does not show it"]
        if mine[0].get("sha256") != result["expected_sha256"]:
            return ["the stored PDF's sha256 is not the one sent"]
        return []

    def details(self, note, result):
        entry = {k: v for k, v in result.items() if k not in ("created", "expected_sha256")}
        return {"attachment": with_links(note, entry), "attachment_id": result.get("id"), "created": result["created"]}

    def done(self, note, result):
        what = result.get("title") or result.get("filename")
        return (f"Attached {what} ({result.get('id')}) to {note}" if result["created"]
                else f"{what} ({result.get('id')}) was already attached to {note}")


class DetachPdf(AttachmentChange):
    name = "notes_detach_pdf"
    description = "notes.detach_pdf: take a PDF off a note's list (--attachment-id); kept aside, the note untouched"
    op = "detach_pdf"

    def prepare(self, note, args):
        return _attachment_id(args)

    def change(self, note, prepared):
        entry = self._p.attachments.remove(note, prepared)
        return {**entry, "id": prepared}

    def confirm(self, result, listed):
        return ["the note still lists it"] if any(a.get("id") == result["id"] for a in listed) else []

    def details(self, note, result):
        return {"attachment_id": result["id"], "removed": True}

    def done(self, note, result):
        return f"Took {result.get('title') or result['id']} off {note} (the PDF is kept aside, not deleted)"
