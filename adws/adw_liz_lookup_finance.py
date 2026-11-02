#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql"]
# ///
"""ADW Liz: Lookup Finance Expense -- supporting evidence from River of Life's books, read only.

One SELECT, through the live-books ADW's own `ReadOnlyBooks` Decorator and
READ ONLY session: 2025 totals for the church-paid categories that make up
part of the pastors' pay (medical, health insurance, life insurance,
vehicles). This is reconciliation evidence only -- the church's books are
never treated as Robert and Rosemary's income, and offerings received are
not on this list at all. If the books can't be reached, that is recorded
and the step still succeeds: nothing on the return depends on it.

    reads:  nonprofit_finance (MySQL, read only)
    writes: finance_evidence

    lancedb_memory/.venv/bin/python adws/adw_liz_lookup_finance.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, read_only_books, run_step
from city_tax.state import WorkflowState

STEP = LizStep( "lookup_finance", WorkflowState.READING_FINANCE, "Read supporting figures from the church's books",
                writes=( "finance_evidence", ))

PATTERNS = ( "%Senior Pastor%", "%Priority Health%", "%Medical%", "%TruStage%", "%Vehicle%", "%Travel & Transportation%" )

SQL = ( "SELECT c.name AS category, COUNT(*) AS expenses, COALESCE( SUM( e.amount ), 0 ) AS total "
        "FROM expenses e JOIN categories c ON c.id = e.category_id "
        "JOIN organizations o ON o.id = e.org_id "
        "WHERE o.`type` = 'NONPROFIT' AND e.expense_role <> 'LINE_ITEM' AND YEAR( e.expense_date ) = 2025 AND ( "
        + " OR ".join( "c.name LIKE %s" for _ in PATTERNS ) + " ) GROUP BY c.name ORDER BY total DESC" )


def _query( kit: Kit, handle ) -> None:
    try:
        rows = read_only_books().fetch( SQL, list( PATTERNS ))
        error = ""
    except Exception as exc:  # noqa: BLE001 -- evidence is optional; say why it's absent
        rows, error = [], f"{ type( exc ).__name__ }: { exc }"
    clean = [{ "category": r[ "category" ], "expenses": int( r[ "expenses" ]), "total": str( r[ "total" ])} for r in rows ]
    kit.save( "finance_evidence", { "rows": clean, "error": error, "note":
              "Church-paid costs that appear inside the pastors' pay. Supporting evidence; not income by itself." })
    handle.log( categories=len( clean ), error=error )
    kit.line = ( f"Read { len( clean )} church-paid benefit categories from the books (read only)." if not error
                 else f"The church's books couldn't be read ({ error[ :80 ]}); the return doesn't depend on them." )


def phases( kit: Kit ):
    return [( "query", "One read-only SELECT of 2025 church-paid benefit categories", _query )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))
