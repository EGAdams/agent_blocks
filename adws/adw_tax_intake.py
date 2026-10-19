#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pillow", "pytesseract", "pymupdf"]
# ///
"""ADW Tax Intake — go through the Barnes' tax-document folders one at a
time, work out what each form still needs, and keep notes.

`rol_finances/readable_documents/tax_documents/` holds one folder per scanned
page, each already carrying a prior extraction. This ADW never re-reads a
scanned image and never decides tax treatment -- it reads what the prior
extraction wrote down, asks a cheap agent what looks missing or uncertain,
and stops for a human to answer or approve before writing anything down. A
folder counts as done once it has its own `intake_notes.txt`; the top-level
`INTAKE_STATUS.txt` is rebuilt from that same fact every run, so the two
can never say different things.

Four phases per folder, repeated up to `--limit` times:

* `survey_N` (code) -- read the folder's existing extraction files and any
  `concerns.md`. No judgement, just what is on disk.
* `extract_needs_N` (agent, `intake`, haiku) -- decide what is missing or
  uncertain from that extraction alone, and what question would resolve
  each gap. Read-only; it never sees the scanned image.
* `review_N` (engineer) -- the human answers the open questions (or approves
  the folder as-is, or leaves it for later). Nothing is written to disk
  until this phase has run.
* `write_notes_N` (code) -- append the folder's findings and the human's
  answers to its `intake_notes.txt`.

A final `update_status` phase rebuilds `INTAKE_STATUS.txt` from whichever
folders now have notes, so it always matches the pending queue exactly.

Usage:
    uv run adws/adw_tax_intake.py "<why this intake pass is running>" \\
        [--base-dir /path/to/tax_documents] [--folder <name>] [--limit 1] \\
        [--auto-decision approve] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> N x (code(survey) -> agent(extract_needs)
        -> engineer(review) -> code(write_notes)) -> code(update_status)
"""

import argparse
import sys
import tempfile
from pathlib import Path

from adw_modules import agents, document_localize, session, tax_intake, utils
from adw_modules.data_types import (AgentCall, ElaborationOutput, FormIntakeOutput,
                                    LocalizationOutput, PhaseParams, TaxIntakeOptions)

REQUIRED_AGENTS = ["intake", "elaborator", "localizer"]


def main(options: TaxIntakeOptions) -> int:
    cfg = agents.load_config(options.config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, options.adw_id)
    base_dir = Path(options.base_dir) if options.base_dir else tax_intake.DEFAULT_BASE_DIR

    dtn = "Record which tax-document folders this pass will survey and under what limit"
    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer, description=dtn)) as ph:
        ph.log(input=options.prompt, base_dir=str(base_dir), folder=options.folder or "(next pending)",
              limit=options.limit, auto_decision=options.auto_decision or "(interactive)")

    if options.folder:
        target = base_dir / options.folder
        queue = [target] if target.is_dir() else []
    else:
        queue = tax_intake.list_pending_folders(base_dir)[:options.limit]

    if not queue:
        dtn = "Confirm there is no folder left to survey before touching the status file"
        with run.phase(PhaseParams(name="nothing_pending", kind="code", owner="tax_intake", description=dtn)) as ph:
            ph.log(message="no matching folder to survey")
        tax_intake.rebuild_status_file(base_dir)
        return run.finish(accepted=True, reason="No pending tax-document folders to survey.")

    results = []
    for i, folder in enumerate(queue, start=1):
        dtn = "Read what is already on disk for this form folder before any agent looks at it"
        with run.phase(PhaseParams(name=f"survey_{i}", kind="code", owner="tax_intake", description=dtn)) as ph:
            survey = tax_intake.survey_folder(folder)
            ph.log(folder=survey.name, images=len(survey.image_files), data_files=len(survey.data_files),
                  has_concerns=survey.has_concerns)

        dtn = "Decide what information this form still needs, from its existing extraction alone"
        with run.phase(PhaseParams(name=f"extract_needs_{i}", kind="agent", owner="intake", description=dtn)) as ph:
            output = ph.call(AgentCall(output_type=FormIntakeOutput,
                                       prompt=tax_intake.build_intake_prompt(survey, options.prompt)))
            ph.log(complete=output.complete, needed=len(output.needed_items),
                  questions=len(output.open_questions), reread_recommended=output.reread_recommended)

        action_seq = [0]

        def elaborate_question(question: str) -> None:
            """ELI5 explanation of one open question, on the human's request."""
            action_seq[0] += 1
            dtn = "Explain one open question in plain language, on the human's request"
            with run.phase(PhaseParams(name=f"elaborate_{i}_{action_seq[0]}", kind="agent",
                                       owner="elaborator", description=dtn)) as eph:
                elab = eph.call(AgentCall(output_type=ElaborationOutput,
                                          prompt=tax_intake.build_elaboration_prompt(survey, output, question)))
                eph.log(question=question[:60])
            print(tax_intake.format_elaboration(elab))

        def show_question(question: str) -> None:
            """Open the scanned page with the relevant region boxed in red, if
            one can be found -- keyword match first, vision fallback second."""
            action_seq[0] += 1
            if not survey.image_files:
                print("    (no image file in this folder to show)")
                return
            image_path = Path(survey.path) / survey.image_files[0]

            dtn = "Try to find and box the region of the page this question is about"
            with run.phase(PhaseParams(name=f"show_{i}_{action_seq[0]}", kind="code",
                                       owner="tax_intake", description=dtn)) as sph:
                box = document_localize.keyword_match_box(image_path, question)
                sph.log(matched=box.found, note=box.note)

            if not box.found:
                dtn = "Ask the vision fallback where on the page this question's answer likely is"
                with run.phase(PhaseParams(name=f"localize_{i}_{action_seq[0]}", kind="agent",
                                           owner="localizer", description=dtn)) as lph:
                    loc = lph.call(AgentCall(output_type=LocalizationOutput,
                                             prompt=tax_intake.build_localization_prompt(
                                                 survey, str(image_path), question)))
                    lph.log(found=loc.found)
                box = document_localize.BoxResult(found=loc.found, x0=loc.x0, y0=loc.y0,
                                                  x1=loc.x1, y1=loc.y1, note=loc.reasoning)

            dtn = "Draw the matched region in red and open the page for the human"
            with run.phase(PhaseParams(name=f"annotate_{i}_{action_seq[0]}", kind="code",
                                       owner="tax_intake", description=dtn)) as aph:
                scratch = Path(tempfile.mkdtemp(prefix="tax-doc-"))
                boxed_path = document_localize.annotate(image_path, box, scratch)
                message = document_localize.open_in_windows(boxed_path or image_path)
                aph.log(boxed=bool(boxed_path), opened=message)
            suffix = "" if box.found else " (no region matched -- showing the full page)"
            print(f"    {message}{suffix}")

        dtn = "Ask the human to answer or approve what this form folder still needs before anything is recorded"
        with run.phase(PhaseParams(name=f"review_{i}", kind="engineer", owner=run.engineer, description=dtn)) as ph:
            print(tax_intake.format_report(survey, output))
            decision = tax_intake.collect_decision(output, options.auto_decision,
                                                   elaborate=elaborate_question, show=show_question)
            ph.log(approved=decision.approved, answers=len(decision.answers),
                  human_notes=decision.human_notes or "(none)")

        dtn = "Record this folder's findings and the human's answers to its intake_notes.txt"
        with run.phase(PhaseParams(name=f"write_notes_{i}", kind="code", owner="tax_intake", description=dtn)) as ph:
            result = tax_intake.write_folder_notes(folder, survey, output, decision)
            ph.log(folder=result.folder, status=result.status, notes_path=result.notes_path)
        results.append(result)

    dtn = "Regenerate the top-level status file from whichever folders now have notes"
    with run.phase(PhaseParams(name="update_status", kind="code", owner="tax_intake", description=dtn)) as ph:
        status_path = tax_intake.rebuild_status_file(base_dir)
        ph.log(status_path=str(status_path))

    message = "; ".join(r.message for r in results)
    if options.message_out:
        Path(options.message_out).write_text(message + "\n")
    return run.finish(accepted=True, reason=message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--base-dir", default="", help="override the tax_documents root")
    parser.add_argument("--folder", default="",
                        help="survey this one folder by name instead of the next pending one")
    parser.add_argument("--limit", type=int, default=1,
                        help="how many pending folders to survey this run (default 1)")
    parser.add_argument("--auto-decision", default="", choices=["", "approve"],
                        help="skip the interactive review (scripted runs); "
                             "'approve' accepts each folder with no human answers")
    parser.add_argument("--message-out", default="", help="write the operator's summary line here")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(TaxIntakeOptions(
        prompt=utils.resolve_prompt(args.prompt), base_dir=args.base_dir, folder=args.folder,
        limit=args.limit, auto_decision=args.auto_decision, message_out=args.message_out,
        config=args.config, adw_id=args.adw_id)))
