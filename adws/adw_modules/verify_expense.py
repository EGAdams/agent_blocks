"""Bridge to the verification rules, which live in the rol_finances repo.

Unlike `calculate_distance.py`, which holds its own logic, this module holds
almost none. The checks are domain rules about one organization's chart of
accounts, they need the repo's fakes and its pytest setup to be worth
anything, and they are called from a menu as well as from here -- so they live
in `rol_finances/expense_review/verification/` and this module is the adapter
that makes them available as ADW code phases.

The import is by `sys.path`, the same way `calculate_distance.py` reaches
`web_browser_manager`: `expense_review` is not installed anywhere and is not
meant to be, and importing it as a package from its repo root is how the menu
loads it too.

Nothing here writes. The verdict is a reading of the stored row, and a reading
that could change what it read would be worth nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Where `expense_review` lives. Duplicated rather than imported for the same
#: reason `calculate_distance.HOME_ADDRESS` is: resolving it would mean
#: importing the thing this constant exists to find.
ROL_FINANCES = Path.home() / "rol_finances"


def _repo_on_path() -> None:
    if str(ROL_FINANCES) not in sys.path:
        sys.path.insert(0, str(ROL_FINANCES))


def load() -> tuple:
    """`(verifier, repository)`, both ready to use.

    Raises if the database is unreachable or the rules file is missing -- a
    verification run that cannot read either has nothing to say, and saying it
    cheerfully would be worse than the traceback.
    """
    _repo_on_path()
    from expense_review.connection import resolve_connection_factory
    from expense_review.mysql_repository import (MySqlExpenseReviewRepository,
                                                 build_taxonomy)
    from expense_review.verification.verifier import ExpenseVerifier
    from expense_review.verification.yaml_rules import YamlVendorRuleStore

    connection_factory = resolve_connection_factory()
    repository = MySqlExpenseReviewRepository(
        connection_factory, build_taxonomy(connection_factory))
    return ExpenseVerifier(YamlVendorRuleStore()), repository


def survey_modules() -> tuple:
    """`(survey, traversal, journal)`, the three modules the whole-season pass
    drives. Returned as modules rather than classes because the orchestrator
    builds several objects out of each, and listing every name here would mean
    editing this file every time one is added."""
    _repo_on_path()
    from expense_review.verification import journal, survey, traversal
    return survey, traversal, journal


def season(year: int) -> tuple:
    """`(start, end)` for a calendar year.

    Taken from `expense_review.app`, which already defines the 2025 season the
    three existing screens use, so the survey covers exactly the rows those
    screens walk rather than a second opinion about what "2025" means.
    """
    _repo_on_path()
    from datetime import date

    from expense_review import app
    if year == app.SEASON_START.year:
        return app.SEASON_START, app.SEASON_END
    return date(year, 1, 1), date(year, 12, 31)


def category_labels(repository, ids) -> dict:
    """`{id: "Name (Parent) -> Report label"}` for the ids given.

    The judge is asked whether a purchase belongs in category 140 rather than
    130, and bare numbers make that unanswerable -- the first live run abstained
    for exactly that reason, correctly: "the permitted categories are not
    defined in the evidence". The names come from the same
    `selectable_categories()` the Recategorize screen lists, so the words the
    agent reasons over are the words the operator will see.
    """
    wanted = {int(i) for i in ids}
    labels = {}
    for category in repository.selectable_categories():
        if category.id in wanted:
            labels[category.id] = (f"{category.name} ({category.parent_name})"
                                   f" -> {category.report_label}")
    return labels


def judge_prompt(evidence, verdict, labels: dict | None = None) -> str:
    """What the judge agent is asked.

    Written to make abstaining easy. The agent is handed the row, the words
    left in its description once the vendor's own name is gone, and the exact
    set of categories the rule permits -- and told in as many words that
    "nothing here says" is a correct and expected answer. An agent that feels
    obliged to produce a category will produce one, and this pass exists
    because that is precisely what must not happen.
    """
    expense = evidence.expense
    known = labels or {}
    permitted = "\n".join(
        f"                       {i}  {known.get(i, '(unnamed category)')}"
        for i in verdict.permitted).lstrip()
    assigned = verdict.assigned_category_id
    assigned_label = known.get(assigned, "(unnamed category)")
    return f"""\
Decide whether one expense's assigned category is supported by its own evidence.

Vendor key:            {verdict.vendor_key}
Permitted categories:  {permitted}
Assigned category:     {assigned}  {assigned_label}
Date:                  {expense.expense_date}
Amount:                {expense.amount}
Description:           {expense.description or "(none)"}
Purchase detail:       {evidence.purchase_detail or "(none)"}
Receipt:               {expense.receipt_url or "(none)"}
Statement:             {expense.scanned_statement_url or "(none)"}
Mom's ledger:          {expense.moms_ledger or "(none)"}

Why this is being asked: {verdict.reason}

This vendor legitimately files under several categories, so the fact that the
assigned category is permitted proves nothing on its own. The question is
narrower: do the words above say what was actually bought, clearly enough that
the assigned category follows from them?

Answer in one of exactly two ways.

* The evidence names a purchase that plainly belongs in the assigned category
  {verdict.assigned_category_id}: set abstained=false, category_id={verdict.assigned_category_id},
  and quote in `reading` the words that decided it.
* Anything else -- the description is only a merchant name and a store number,
  the detail could as easily support another permitted category, the receipt
  would have to be opened to tell: set abstained=true and say in `summary` what
  you could not read.

Abstaining is the expected answer for most rows and costs nothing. Do not
choose a category that is not {verdict.assigned_category_id}; a different
category is a correction for a person to make, not a reading. Do not infer a
purpose from the amount alone.
"""
