"""Shared async test harness: discovers module-level test_* coroutines.

Lives beside tests/fakes.py because it is the same kind of thing -- shared
test machinery every composition root's offline tests can use, imported as
`from tests.runner import main` with lancedb_memory as the cwd.

mazda_team/tests/runner.py is an older, identical copy that predates this
one; its tests still import their own. New test files should use this.
"""
import asyncio
import sys


async def _run( namespace: dict ) -> int:
    tests = [ v for k, v in sorted( namespace.items()) if k.startswith( "test_" )]
    failures = 0
    for test in tests:
        try:
            await test()
            print( f"PASS  { test.__name__ }" )
        except AssertionError as exc:
            failures += 1
            print( f"FAIL  { test.__name__ }: { exc }" )
    print( f"\n{ len( tests ) - failures }/{ len( tests )} passed" )
    return 1 if failures else 0


def main( namespace: dict ) -> None:
    sys.exit( asyncio.run( _run( namespace )))
