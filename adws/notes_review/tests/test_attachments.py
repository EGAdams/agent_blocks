"""The notes.* PDF attachment operations over fakes: attach, list, inspect, detach -- and the note never moves."""
import hashlib
from pathlib import Path

from notes_review.catalog import COMMANDS, PUBLIC, READS

from .fakes import DEED, DEED_NAME, MemoryAttachments, plane

ADWS = Path(__file__).resolve().parents[2]
PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\ntrailer << >>\n%%EOF\n"


def attached_plane(tmp_path, **kw):
    p, store = plane(tmp_path)
    p.attachments = MemoryAttachments(store, **kw)
    return p, store


def run(p, public, **args):
    return COMMANDS[public](p).run({"note": DEED_NAME, "adw_id": "abcd1234", **args})


def pdf_file(tmp_path, data=PDF, name="Personal_Representative_Deed_for_Notary_REVISED.pdf"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_attach_list_inspect_detach_and_the_note_never_changes(tmp_path):
    p, store = attached_plane(tmp_path)
    sha = hashlib.sha256(PDF).hexdigest()
    out = run(p, "notes.attach_pdf", pdf_file=pdf_file(tmp_path), title="Latest Printable Deed PDF",
              upload_sha256=sha)
    assert out.ok and out.data["verified"] and out.data["note_unchanged"] and out.data["created"]
    ident = out.data["attachment_id"]
    assert out.data["attachment"]["filename"] == "Personal_Representative_Deed_for_Notary_REVISED.pdf"
    assert out.data["attachment"]["view_url"].endswith(f"id={ident}")
    assert out.data["attachment"]["download_url"].endswith("&download=1")
    assert out.data["active_text"] and out.data["highlight_status"] == "blinking"     # review state reported
    again = run(p, "notes.attach_pdf", pdf_file=pdf_file(tmp_path), title="ignored")
    assert again.ok and not again.data["created"] and "already attached" in again.line

    listed = run(p, "notes.attachments")
    assert listed.ok and [a["id"] for a in listed.data["attachments"]] == [ident]
    info = run(p, "notes.attachment", attachment_id=ident)
    assert info.ok and "intact" in info.line

    gone = run(p, "notes.detach_pdf", attachment_id=ident)
    assert gone.ok and gone.data["verified"] and gone.data["attachments"] == []
    assert store.notes[DEED_NAME] == DEED and store.writes == 0


def test_what_is_not_a_whole_matching_pdf_is_refused_before_anything_is_attached(tmp_path):
    p, _ = attached_plane(tmp_path)
    assert run(p, "notes.attach_pdf").data["cause"] == "no_pdf"
    assert run(p, "notes.attach_pdf", pdf_file=str(tmp_path / "absent.pdf")).data["cause"] == "no_pdf"
    assert run(p, "notes.attach_pdf", pdf_file=pdf_file(tmp_path, b"<html>%%EOF", "x.pdf")).data["cause"] == "not_a_pdf"
    assert run(p, "notes.attach_pdf", pdf_file=pdf_file(tmp_path),
               upload_sha256="0" * 64).data["cause"] == "hash_mismatch"
    assert p.attachments.items == {}


def test_a_revision_that_moved_or_a_store_that_lies_is_not_verified(tmp_path):
    p, _ = attached_plane(tmp_path, touch=True)
    out = run(p, "notes.attach_pdf", pdf_file=pdf_file(tmp_path))
    assert not out.ok and out.data["cause"] == "not_verified" and not out.data["note_unchanged"]
    p, _ = attached_plane(tmp_path, lie=True)
    out = run(p, "notes.attach_pdf", pdf_file=pdf_file(tmp_path))
    assert not out.ok and "sha256" in out.line


def test_missing_attachments_notes_and_wiring_are_honest_refusals(tmp_path):
    p, _ = attached_plane(tmp_path)
    assert run(p, "notes.attachment", attachment_id="nope").data["cause"] == "no_attachment"
    assert run(p, "notes.attachment", attachment_id="a-000000000000").data["cause"] == "attachment_not_found"
    assert run(p, "notes.detach_pdf", attachment_id="a-000000000000").data["cause"] == "attachment_not_found"
    assert run(p, "notes.attachments", note="2099-01-01_absent.md").data["cause"] == "note_not_found"
    bare, _ = plane(tmp_path)
    assert run(bare, "notes.attachments").data["cause"] == "no_attachments"


def test_each_attachment_operation_is_registered_and_has_its_own_adw():
    for public in ("notes.attach_pdf", "notes.attachments", "notes.attachment", "notes.detach_pdf"):
        assert public in PUBLIC
        thin = ADWS / f"adw_{public.replace('.', '_')}.py"
        assert thin.exists() and f'fixed="{public}"' in thin.read_text()
    assert "notes.attachments" in READS and "notes.attach_pdf" not in READS


def test_the_store_names_the_bridge_task_it_runs_for(monkeypatch):
    from notes_review.store import bridge_from_environment, bridge_task
    monkeypatch.delenv("NOTES_BRIDGE_TASK", raising=False)
    assert bridge_task() == "notes-review"
    monkeypatch.setenv("NOTES_BRIDGE_TASK", "t_" + "b" * 24)
    assert bridge_from_environment().task == "t_" + "b" * 24          # nothing is connected to read this
    monkeypatch.setenv("NOTES_BRIDGE_TASK", "../../etc/passwd")
    assert bridge_task() == "notes-review"
