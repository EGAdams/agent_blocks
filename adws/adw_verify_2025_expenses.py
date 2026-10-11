#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql"]
# ///
"""ADW Verify 2025 Expenses (survey) — walk every 2025 expense, verify each,
and report what the checks said. Writes no expense and no vendor rule.

This is the orchestrator for the automatic pass, shipped read-only first. The
interactive version stops on a discrepancy and asks a person; this one cannot
stop, because it has no console to stop at. That is the point: before anything
is allowed to mark a row verified or correct a category, we want the shape of
the whole year's answer -- how many rows pass, how many would halt a person,
and which of the two kinds of halt they are. A missing vendor rule is fixed
once in a YAML file and clears hundreds of rows; a wrong category is fixed one
row at a time. The same halt count can mean an afternoon or a month.

Three phases, all code. There is no agent phase and no `--judge`: a judgement
costs a few thousand tokens, and asking it 2,000 times to produce a number
nobody acts on yet would be the expensive way to learn nothing.

* `traverse` (code) — read the ordered sequence, decide where to start.
  `--resume` picks up after the last settled position in the journal, and
  refuses if the sequence has changed length underneath it.
* `survey` (code) — verify each expense in order, journal each verdict.
* `report` (code) — the breakdown, worst bucket first.

The run is `accepted` when every expense was examined, NOT when they all
passed. A year in which 1,900 rows need a person is a successful survey and a
large amount of work; conflating the two would make the one number that
matters invisible.

Usage:
    uv run adws/adw_verify_2025_expenses.py "survey 2025 before any writes" \\
        [--year 2025] [--kind all|unverified|backlog] [--limit 50] \\
        [--resume] [--run-id <id>] [--message-out /tmp/line.txt]

Phases: code(traverse) -> code(survey) -> code(report)
"""

import argparse
import sys
from pathlib import Path

from adw_modules import agents, session, utils, verify_expense
from adw_modules.data_types import PhaseParams, SurveyExpensesOptions


def main(options: SurveyExpensesOptions) -> int:
    cfg = agents.load_config(options.config)
    agents.validate(cfg, [])
    run = session.ensure(cfg, options.adw_id)

    verifier, repository = verify_expense.load()
    survey_mod, traversal_mod, journal_mod = verify_expense.survey_modules()
    start, end = verify_expense.season(options.year)

    with run.phase(PhaseParams(name="traverse", kind="code", owner="verify",
                               description="Read the ordered 2025 sequence and decide which position to start from")) as ph:
        traversal = traversal_mod.SeasonTraversal(repository, start, end, options.kind)
        run_id = options.run_id or run.adw_id
        journal = journal_mod.JsonlRunJournal(
            journal_mod.run_path(run_id), kind=options.kind,
            start=start, end=end, total=traversal.total(), dry_run=True)
        journal.open()
        resume_after = None
        if options.resume:
            ok, why = journal.matches(options.kind, traversal.total())
            if not ok:
                raise RuntimeError(f"cannot resume {journal.path.name}: {why}")
            resume_after = journal.last_position()
        ph.log(kind=options.kind, season=f"{start}..{end}",
               total=traversal.total(), journal=str(journal.path),
               resume_after="(from the start)" if resume_after is None else resume_after,
               limit=options.limit or "(no limit)", input=options.prompt)

    with run.phase(PhaseParams(name="survey", kind="code", owner="verify",
                               description="Verify each expense in order and journal every verdict; change nothing")) as ph:
        result = survey_mod.Survey(repository, verifier, traversal, journal).run(
            limit=options.limit, resume=options.resume)
        ph.log(examined=result.examined, **result.report.counts())

    with run.phase(PhaseParams(name="report", kind="code", owner="verify",
                               description="Break the stops down by which check failed, worst bucket first")) as ph:
        for line in result.lines:
            ph.log(bucket=line)
        for bucket, reason in sorted(result.first_reason.items()):
            if bucket != "passed":
                ph.log(**{f"why {bucket}": reason})
        ph.log(journal=str(journal.path))

    #: Examined, not passed. See the module docstring. A run cut short by
    #: `--limit` did what it was asked, so it is accepted; only an unexplained
    #: early stop is a failed survey.
    examined_everything = result.limited or not result.report.stopped_early
    line = (f"surveyed {result.examined} of {traversal.total()} {options.kind} "
            f"expenses in {options.year}: {result.report.line}")
    if options.message_out:
        Path(options.message_out).write_text(line + "\n")
    return run.finish(accepted=examined_everything, reason=line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--kind", default="all",
                        choices=("all", "unverified", "backlog"),
                        help="which sequence to walk (default all, including "
                             "rows already marked verified)")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many expenses, for a smoke test")
    parser.add_argument("--resume", action="store_true",
                        help="continue after the last settled position in the journal")
    parser.add_argument("--run-id", default="",
                        help="journal file name (default: the adw id)")
    parser.add_argument("--message-out", default="",
                        help="write the operator's one line here")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(SurveyExpensesOptions(
        prompt=utils.resolve_prompt(args.prompt), year=args.year, kind=args.kind,
        limit=args.limit, resume=args.resume, run_id=args.run_id,
        message_out=args.message_out, config=args.config, adw_id=args.adw_id)))
