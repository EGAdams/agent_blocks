"""Survey one tax-document folder at a time: what it already has, what it
still needs, and what the human decided about it.

`rol_finances/readable_documents/tax_documents/` holds one folder per scanned
page, each already carrying a prior extraction (`*_Summary.json`, or the
newer `summary.md`/`detail.md`/`extracted_fields.json` split) and sometimes a
`concerns.md` flagging a discrepancy. This module never re-reads the scanned
image and never decides tax treatment -- it reads what a prior extraction
pass already wrote down, asks the `intake` agent what is still missing, and
records a human's answers. Re-reading a page or reasoning about clergy
housing/Schedule C questions is someone else's job (`gemini_photo_reader.py`,
`/tax-doc-scan`, the `barnes-tax-preparation` skill).

A folder counts as intaken once it has an `intake_notes.txt` -- the one flag
`list_pending_folders` checks, so the status file and the pending queue can
never disagree about what has been looked at.
"""

from __future__ import annotations

from pathlib import Path

from .data_types import (ElaborationOutput, FolderIntakeResult, FormDecision,
                         FormFolderSurvey, FormIntakeOutput, HumanAnswer)
from .utils import now_iso

DEFAULT_BASE_DIR = Path.home() / "rol_finances" / "readable_documents" / "tax_documents"

NOTES_FILENAME = "intake_notes.txt"
STATUS_FILENAME = "INTAKE_STATUS.txt"
CONCERNS_FILENAME = "concerns.md"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf"}
DATA_SUFFIXES = {".json", ".md"}

#: Keeps the agent's prompt (and its context window) bounded. Per-document
#: extractions are small; a folder legitimately this large gets flagged by
#: the agent as "could not read it all" rather than silently truncated mid-field.
MAX_EXTRACTED_CHARS = 12_000


def list_form_folders(base_dir: Path) -> list[Path]:
    """Every immediate subdirectory -- one per scanned page. Top-level files
    like INDEX.md or gemini_photo_reader.py are never directories, so they
    are excluded by this check alone."""
    if not base_dir.is_dir():
        return []
    return sorted(p for p in base_dir.iterdir() if p.is_dir())


def is_already_intaken(folder: Path) -> bool:
    return (folder / NOTES_FILENAME).exists()


def list_pending_folders(base_dir: Path) -> list[Path]:
    return [f for f in list_form_folders(base_dir) if not is_already_intaken(f)]


def survey_folder(folder: Path) -> FormFolderSurvey:
    """Read what is already on disk for one folder -- no judgement, just facts."""
    image_files = sorted(p.name for p in folder.iterdir()
                         if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    data_files = sorted(p.name for p in folder.iterdir()
                        if p.is_file() and p.suffix.lower() in DATA_SUFFIXES
                        and p.name not in (NOTES_FILENAME, CONCERNS_FILENAME))

    concerns_path = folder / CONCERNS_FILENAME
    has_concerns = concerns_path.exists()
    concerns_text = concerns_path.read_text(errors="replace")[:4_000] if has_concerns else ""

    chunks: list[str] = []
    total = 0
    for name in data_files:
        text = (folder / name).read_text(errors="replace")
        chunk = f"--- {name} ---\n{text}\n"
        if total + len(chunk) > MAX_EXTRACTED_CHARS:
            chunks.append(f"--- ({len(data_files) - len(chunks)} more file(s) omitted, over budget) ---")
            break
        chunks.append(chunk)
        total += len(chunk)

    return FormFolderSurvey(
        name=folder.name, path=str(folder), image_files=image_files, data_files=data_files,
        has_concerns=has_concerns, concerns_text=concerns_text,
        extracted_text="\n".join(chunks), already_intaken=is_already_intaken(folder))


def build_intake_prompt(survey: FormFolderSurvey, request_prompt: str) -> str:
    """The task text the `intake` agent sees as `{{prompt}}` -- the survey's
    facts, not a description of the folder, since the agent cannot browse it."""
    lines = [
        request_prompt.strip(),
        "",
        f"Folder: {survey.name}",
        f"Image file(s) on disk (not visible to you): {', '.join(survey.image_files) or '(none)'}",
        f"Extraction file(s) read: {', '.join(survey.data_files) or '(none)'}",
    ]
    if survey.has_concerns:
        lines += ["", f"concerns.md for this folder:", survey.concerns_text]
    lines += ["", "Extracted content:", survey.extracted_text or "(no extraction files found)"]
    return "\n".join(lines)


def build_elaboration_prompt(survey: FormFolderSurvey, output: FormIntakeOutput, question: str) -> str:
    """The task text the `elaborator` agent sees -- the same survey facts the
    `intake` agent had, plus the one question the human wants explained."""
    lines = [
        f"Folder: {survey.name}", f"Form: {output.form_name or '(not named)'}",
        f"open_question: {question}",
    ]
    if output.needed_items:
        lines += ["", "needed_items:"] + [f"  - {item}" for item in output.needed_items]
    if output.reread_recommended:
        lines += ["", f"reread_reason: {output.reread_reason}"]
    lines += ["", "Extracted content:", survey.extracted_text or "(no extraction files found)"]
    return "\n".join(lines)


def build_localization_prompt(survey: FormFolderSurvey, image_path: str, question: str) -> str:
    """The task text the `localizer` agent sees -- an image path to read with
    its own `read` tool, and the one question it is looking for on that page."""
    return "\n".join([f"Folder: {survey.name}", f"Image file to read: {image_path}",
                      f"open_question: {question}"])


def format_elaboration(elab: ElaborationOutput) -> str:
    """What the human sees after asking to elaborate on one question."""
    return f"    ELI5: {elab.explanation}\n    Try this: {elab.suggested_step}"


def format_report(survey: FormFolderSurvey, output: FormIntakeOutput) -> str:
    """The numbered list shown to the human in the review phase."""
    lines = [f"\n{survey.name} -- {output.form_name or '(form not named)'}",
             f"  complete: {output.complete}"]
    if output.reread_recommended:
        lines.append(f"  RE-READ RECOMMENDED: {output.reread_reason}")
    if output.needed_items:
        lines.append("  Needed:")
        lines += [f"    - {item}" for item in output.needed_items]
    if output.open_questions:
        lines.append("  Questions:")
        lines += [f"    {i}. {q}" for i, q in enumerate(output.open_questions, start=1)]
    return "\n".join(lines)


def collect_decision(output: FormIntakeOutput, auto_decision: str,
                     ask: "Callable[[str], str] | None" = None,
                     elaborate: "Callable[[str], None] | None" = None,
                     show: "Callable[[str], None] | None" = None) -> FormDecision:
    """Ask the human to answer each open question and approve the folder, or
    take the scripted `auto_decision` path for a non-interactive run.

    `ask` defaults to `input` and is swapped out in tests -- the same seam
    `adw_receipt_locate.py`'s `--auto-pick` covers with a CLI flag instead.
    `elaborate`/`show`, when supplied, are the two review-time actions a human
    can ask for instead of an answer: an ELI5 explanation, or the scanned page
    opened with the relevant region boxed. Each call blocks until done, then
    this loop re-prompts the same question -- neither one consumes an answer.
    Omitting either (the default) makes that action a no-op for callers that
    have nowhere to route an agent call from, such as tests.
    """
    if auto_decision == "approve":
        return FormDecision(approved=True, human_notes="(auto-approved, no interactive review)")
    ask = ask or input
    answers: list[HumanAnswer] = []
    for q in output.open_questions:
        while True:
            raw = ask(f"  > {q}\n    answer (blank to leave open), "
                      f"or 'e' to elaborate / 's' to show the document: ")
            cmd = raw.strip().lower()
            if cmd in ("e", "elaborate") and elaborate:
                elaborate(q)
                continue
            if cmd in ("s", "show") and show:
                show(q)
                continue
            answers.append(HumanAnswer(item=q, answer=raw))
            break
    approve_raw = ask("Mark this folder reviewed and move on? [y/N]: ").strip().lower()
    notes = ask("Any other notes for this folder (blank for none): ").strip()
    return FormDecision(approved=approve_raw in ("y", "yes"), answers=answers, human_notes=notes)


def write_folder_notes(folder: Path, survey: FormFolderSurvey, output: FormIntakeOutput,
                       decision: FormDecision) -> FolderIntakeResult:
    """Append one timestamped entry to the folder's own intake_notes.txt.

    Append, not overwrite: a folder can be revisited (--folder) once new
    information arrives, and the prior pass's record should survive that.
    """
    unanswered = [a.item for a in decision.answers if not a.answer.strip()]
    status = "complete" if (output.complete and decision.approved) else "needs_info"

    entry = [f"=== {now_iso()} ===", f"form_name: {output.form_name or '(not named)'}",
             f"status: {status}", f"image_files: {', '.join(survey.image_files) or '(none)'}"]
    if output.reread_recommended:
        entry.append(f"reread_recommended: {output.reread_reason}")
    if output.needed_items:
        entry.append("needed_items:")
        entry += [f"  - {item}" for item in output.needed_items]
    if decision.answers:
        entry.append("human_answers:")
        entry += [f"  Q: {a.item}\n  A: {a.answer or '(left open)'}" for a in decision.answers]
    if decision.human_notes:
        entry.append(f"human_notes: {decision.human_notes}")
    if unanswered:
        entry.append(f"still_open: {len(unanswered)} question(s) unanswered")
    entry.append("")

    notes_path = folder / NOTES_FILENAME
    with notes_path.open("a") as f:
        f.write("\n".join(entry) + "\n")

    message = f"{survey.name}: {status}" + (f" ({len(unanswered)} still open)" if unanswered else "")
    return FolderIntakeResult(folder=survey.name, status=status, notes_path=str(notes_path),
                              message=message)


def rebuild_status_file(base_dir: Path) -> Path:
    """Regenerate the top-level status file from what is actually on disk.

    Rebuilt wholesale rather than appended-to: the ground truth is always
    "which folders have an intake_notes.txt right now", and recomputing it
    each run means the status file can never drift from that truth the way an
    incrementally-patched summary could.
    """
    folders = list_form_folders(base_dir)
    done = [f.name for f in folders if is_already_intaken(f)]
    pending = [f.name for f in folders if not is_already_intaken(f)]

    lines = [
        "# Tax Document Intake Status", "",
        f"Last updated: {now_iso()}",
        f"Processed: {len(done)}/{len(folders)} folder(s)", "",
        "## Done",
    ]
    lines += [f"- {name}" for name in done] if done else ["(none yet)"]
    lines += ["", "## Pending (next up first)"]
    lines += [f"- {name}" for name in pending] if pending else ["(none -- every folder has been surveyed)"]
    lines += ["", "## What needs to be done next"]
    if pending:
        lines.append(f"Run `adws/adw_tax_intake.py` again to survey the next pending folder "
                     f"(currently: {pending[0]}), or pass --folder <name> to pick one specifically.")
    else:
        lines.append("Every folder has an intake_notes.txt. Re-run with --folder <name> on any "
                     "folder where new information has since arrived, to record a follow-up pass.")
    lines.append("Open questions left unanswered inside any folder's intake_notes.txt still need a "
                 "human answer -- grep for 'still_open' across the tree to find them.")

    status_path = base_dir / STATUS_FILENAME
    status_path.write_text("\n".join(lines) + "\n")
    return status_path


__all__ = ["DEFAULT_BASE_DIR", "NOTES_FILENAME", "STATUS_FILENAME", "build_elaboration_prompt",
          "build_intake_prompt", "build_localization_prompt", "collect_decision",
          "format_elaboration", "format_report", "is_already_intaken", "list_form_folders",
          "list_pending_folders", "rebuild_status_file", "survey_folder", "write_folder_notes"]
