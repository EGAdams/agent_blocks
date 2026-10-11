#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql"]
# ///
"""ADW Verify Expense — check that one expense's vendor key, its vendor's
category rule, and the category it is actually filed under all agree.

The smallest of the verification workflows and the one every other depends on:
one expense in, one verdict out, nothing written. A vendor key having a
category mapping is not sufficient for a pass — the key has to be right for
the row, the rule has to be right for the key, and the row's category has to
be supported by what the row itself says.

Four phases, and the agent one usually does not run:

* `request` (engineer) — which expense, under which rules, with which judge.
* `verify_vendor` (code) — is the key the alias rules derive from this
  description the right one. Two things can be said mechanically and both are
  said plainly: no alias matched at all, or a registered account (`x6642` is
  the church's water bill) says one thing while the aliases say another. A
  failure here ends the run, because "wrong category for which vendor?" has
  no answer.
* `verify_category` (code) — evaluate the vendor's rule. A single-category
  vendor is an equality. A multiple-category vendor (Meijer sells both a
  wrench for the church sink and an ice cream cone) never passes on this
  phase alone, by design.
* `judge` (agent, `--judge`, default `judge`) — only for a multiple-category
  row whose description actually carries a purchase detail. Gated so it can
  only confirm the category already assigned, never widen the permitted set
  and never name a different one; abstaining passes the gate outright. Pass
  `--judge ""` for a code-only run, which is what a survey pass wants.

There is deliberately no fifth phase that fixes anything. Resolving a
discrepancy is a separate workflow, because it writes, and keeping the reading
and the writing in different scripts is what makes it impossible for this one
to quietly change a vendor key or a category.

Usage:
    uv run adws/adw_verify_expense.py "<why this expense is being verified>" \\
        --expense-id 1130 [--judge scout] \\
        [--message-out /tmp/line.txt] [--verdict-out /tmp/verdict.json] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(verify_vendor) [-> code(verify_category) [-> <judge>]]
"""

import argparse
import sys
from pathlib import Path

from adw_modules import agents, gates, session, utils, verify_expense
from adw_modules.data_types import (AgentCall, CategoryOpinion, PhaseParams,
                                    VerifyExpenseOptions)

#: Haiku, `tools: []`, `writes: []` in the config -- a dedicated agent rather
#: than one of the recon agents, and the empty tool list is the whole point.
#: Every field the question needs is already in the prompt, so an agent with
#: `ls`/`find`/`grep` spends its budget hunting for a codebase that is not
#: there: measured against `scout`, one expense cost 158,517 tokens and $0.0658
#: and came back describing the prompt instead of answering it. The same
#: expense through `judge` cost 3,329 tokens and $0.0057 and answered. Across a
#: 2,000-row pass that difference is about $130.
DEFAULT_JUDGE = "judge"


def main(options: VerifyExpenseOptions) -> int:
    cfg = agents.load_config(options.config)
    agents.validate(cfg, [options.judge] if options.judge else [])
    run = session.ensure(cfg, options.adw_id)

    verifier, repository = verify_expense.load()
    expense = repository.read(options.expense_id)
    evidence = verifier.evidence_for(expense)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Record which expense is being verified, and under which vendor key and judge")) as ph:
        ph.log(input=options.prompt, expense_id=expense.id,
               expense_date=str(expense.expense_date), amount=str(expense.amount),
               description=expense.description or "(none)",
               vendor_key=evidence.vendor_key or "(unresolved)",
               account=evidence.account_last4 or "(none)",
               assigned_category=expense.category_id,
               judge=options.judge or "(code only)")

    with run.phase(PhaseParams(name="verify_vendor", kind="code", owner="verify",
                               description="Decide whether the vendor key the alias rules derive from this description is the right one for this row")) as ph:
        verdict = verifier.verify(expense)
        ph.log(status=verdict.vendor_status, key=verdict.vendor_key or "(none)",
               reason=verdict.reason if verdict.failing_check == "vendor" else "resolved")

    if verdict.failing_check == "vendor":
        return _finish(run, options, verdict)

    with run.phase(PhaseParams(name="verify_category", kind="code", owner="verify",
                               description="Evaluate the vendor's category rule against the category this row is actually filed under")) as ph:
        ph.log(status=verdict.category_status,
               permitted=list(verdict.permitted) or "(no rule)",
               assigned=verdict.assigned_category_id, passed=verdict.passed,
               reason=verdict.reason)

    #: The one judgement call in the whole pass. A row only reaches it when the
    #: vendor permits several categories AND the description says something
    #: beyond the merchant's name -- so there is a reading to be had. Everything
    #: else is a stop for a person.
    if options.judge and verdict.category_status == "ambiguous" and _has_detail(evidence):
        with run.phase(PhaseParams(name="judge", kind="agent", owner=options.judge, retries=1,
                                   description="Weigh this row's purchase detail against the categories its vendor permits, or abstain")) as ph:
            opinion = ph.call(AgentCall(
                output_type=CategoryOpinion,
                prompt=verify_expense.judge_prompt(
                    evidence, verdict,
                    verify_expense.category_labels(repository, verdict.permitted)),
                gates=[gates.category_id_is_permitted(verdict.permitted)]))

        from expense_review.verification.verifier import with_judgement
        verdict = with_judgement(verdict, opinion.category_id,
                                 abstained=opinion.abstained,
                                 note=opinion.reading or opinion.summary)

    return _finish(run, options, verdict)


def _has_detail(evidence) -> bool:
    return bool(evidence.purchase_detail)


def _finish(run, options: VerifyExpenseOptions, verdict) -> int:
    """The verdict is the product, so it is written before the run is closed.

    Both outputs are files rather than stdout, the same contract
    `adw_map_open.py` and `adw_calculate_distance.py` use: the caller is a menu
    as often as a terminal, and a menu that had to parse a trace for its answer
    would break the first time a phase logged an extra line.
    """
    if options.message_out:
        Path(options.message_out).write_text(verdict.operator_line + "\n")
    if options.verdict_out:
        Path(options.verdict_out).write_text(_as_json(verdict) + "\n")

    #: A stop is not a failure. The phases all did their jobs; the expense is
    #: what has a problem. `accepted` reflects the expense, and the stop reason
    #: is the run's reason, so the trace reads as "looked, and here is what is
    #: wrong" rather than "crashed".
    return run.finish(accepted=verdict.passed, reason=verdict.operator_line)


def _as_json(verdict) -> str:
    import dataclasses
    import json
    return json.dumps(dataclasses.asdict(verdict), default=str, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--expense-id", type=int, required=True,
                        help="the expense to verify")
    parser.add_argument("--judge", default=DEFAULT_JUDGE,
                        help=f"agent that weighs a multiple-category row "
                             f"(default {DEFAULT_JUDGE}; pass '' for code only)")
    parser.add_argument("--message-out", default="",
                        help="write the operator's one line here, for a non-terminal caller")
    parser.add_argument("--verdict-out", default="",
                        help="write the full verdict here as JSON")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(VerifyExpenseOptions(
        prompt=utils.resolve_prompt(args.prompt), expense_id=args.expense_id,
        judge=args.judge, message_out=args.message_out,
        verdict_out=args.verdict_out, config=args.config, adw_id=args.adw_id)))
