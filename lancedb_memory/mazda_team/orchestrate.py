# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""Composition root for Mazda's independent spreadsheet and tax-form agents.

    .venv/bin/python -m mazda_team.orchestrate
    .venv/bin/python -m mazda_team.orchestrate --only sheeta
    .venv/bin/python -m mazda_team.orchestrate --only tarita
"""
import argparse
import asyncio
import sys
from pathlib import Path

from .adapters.tool_agent import RoleResult, run_role
from .specialist_roles import REPO_ROOT, ROLES


def _report( result: RoleResult ) -> None:
    status = "ERROR" if result.is_error else "ok"
    cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "?"
    print( f"\n=== {result.role_name} [{status}] tool_calls={result.tool_calls} cost={cost} ===" )
    print( f"-> {result.output_path}" )
    print( result.final_text )


async def run_team( model: str, only: list[ str ] | None ) -> int:
    names = only or list( ROLES )
    Path( REPO_ROOT ).mkdir( parents=True, exist_ok=True )
    print( f"Launching in parallel: {names}" )
    results = await asyncio.gather(
        *( run_role( ROLES[ n ], cwd=REPO_ROOT, model=model ) for n in names ),
        return_exceptions=True,
    )
    had_error = False
    for name, result in zip( names, results ):
        if isinstance( result, Exception ):
            had_error = True
            print( f"\n=== {name} [EXCEPTION] ===\n{result}" )
        else:
            had_error = had_error or result.is_error
            _report( result )
    return 1 if had_error else 0


def main() -> None:
    parser = argparse.ArgumentParser( description=__doc__ )
    parser.add_argument( "--model", default="claude-sonnet-5" )
    parser.add_argument(
        "--only", nargs="+", choices=list( ROLES ),
        help="run only these named roles instead of the full team",
    )
    args = parser.parse_args()
    sys.exit( asyncio.run( run_team( args.model, args.only ) ) )


if __name__ == "__main__":
    main()
