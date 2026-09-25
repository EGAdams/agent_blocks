# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""Composition root for the 7-agent vendor-pattern + receipt-audit team.

    .venv/bin/python -m finance_team.orchestrate                  # everything
    .venv/bin/python -m finance_team.orchestrate --only mapper_bank
    .venv/bin/python -m finance_team.orchestrate --only expense_reconciler
    .venv/bin/python -m finance_team.orchestrate --skip-receipts
    .venv/bin/python -m finance_team.orchestrate --skip-expense-check
    .venv/bin/python -m finance_team.orchestrate --model claude-haiku-4-5-20251001

Fan-out/fan-in: the 4 mapper_* agents (one per `expenses.method` value),
receipt_scanner, and expense_reconciler share no state and run concurrently.
synthesizer runs last because it reads the 4 mappers' JSON output files.
"""
import argparse
import asyncio
import sys

from .adapters.tool_agent import RoleResult, run_role
from .roles import REPO_ROOT, ROLES

MAPPER_NAMES = [ "mapper_cash", "mapper_card", "mapper_bank", "mapper_other" ]


def _report( result: RoleResult ) -> None:
    status = "ERROR" if result.is_error else "ok"
    cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "?"
    print(
        f"\n=== {result.role_name} [{status}] tool_calls={result.tool_calls} cost={cost} ==="
    )
    print( f"-> {result.output_path}" )
    print( result.final_text )


async def run_team(
    model: str, only: list[ str ] | None, skip_receipts: bool, skip_expense_check: bool
) -> int:
    names = only or (
        MAPPER_NAMES
        + ( [] if skip_receipts else [ "receipt_scanner" ] )
        + ( [] if skip_expense_check else [ "expense_reconciler" ] )
    )
    parallel_names = [ n for n in names if n != "synthesizer" ]
    want_synthesis = only is None or "synthesizer" in only

    print( f"Launching in parallel: {parallel_names}" )
    results = await asyncio.gather(
        *( run_role( ROLES[ n ], cwd=REPO_ROOT, model=model ) for n in parallel_names ),
        return_exceptions=True,
    )

    had_error = False
    for name, result in zip( parallel_names, results ):
        if isinstance( result, Exception ):
            had_error = True
            print( f"\n=== {name} [EXCEPTION] ===\n{result}" )
        else:
            had_error = had_error or result.is_error
            _report( result )

    ran_all_mappers = all( n in parallel_names for n in MAPPER_NAMES )
    if want_synthesis and ran_all_mappers and not had_error:
        print( "\nAll 4 mappers finished cleanly -- running synthesizer." )
        synth = await run_role( ROLES[ "synthesizer" ], cwd=REPO_ROOT, model=model )
        had_error = had_error or synth.is_error
        _report( synth )
    elif want_synthesis and not ran_all_mappers:
        print(
            "\nSkipping synthesizer: it needs all 4 mapper outputs "
            f"(only ran {parallel_names})."
        )

    return 1 if had_error else 0


def main() -> None:
    parser = argparse.ArgumentParser( description=__doc__ )
    parser.add_argument( "--model", default="claude-sonnet-5" )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=list( ROLES.keys()),
        help="run only these named roles instead of the full team",
    )
    parser.add_argument(
        "--skip-receipts", action="store_true", help="don't run the receipt_scanner agent"
    )
    parser.add_argument(
        "--skip-expense-check", action="store_true",
        help="don't run the expense_reconciler agent",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(
        run_team( args.model, args.only, args.skip_receipts, args.skip_expense_check )
    )
    sys.exit( exit_code )


if __name__ == "__main__":
    main()
