#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Watch -- look at this machine's checks once. No model anywhere in it; cron runs it every 2 minutes.

    adw_machine_watch.py              count failures, and wake the supervisor when a check has failed often enough
    adw_machine_watch.py --dry-run    only look: nothing saved, nobody woken
    adw_machine_watch.py --json       the answer as one JSON line (for another program)

Which machine this is comes from its hostname and machine_supervisor/machines.json. A passing run also starts
the todo worker when todos are queued and retries mail that could not be sent. Exit status: 0 all passing, 1 not.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Look at this machine's checks once.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    adw_id, kit = start("watch" + (" --dry-run" if args.dry_run else ""), quiet=args.json)
    outcome = kit.watch.run(dry_run=args.dry_run)
    failing = [r for r in outcome.results if not r.ok]
    if args.dry_run and not args.json:
        for result in outcome.results:
            print(f"  {'ok  ' if result.ok else 'FAIL'} {result.check}: {result.detail}")
    line = (f"{kit.catalog.me().name}: {len(outcome.results) - len(failing)}/{len(outcome.results)} checks passing"
            + (f"; failing: {', '.join(r.check for r in failing)}" if failing else "")
            + (f"; supervisor woken for {outcome.incident}" if outcome.incident else ""))
    return finish(args.json_out, not failing, line, outcome.model_dump(mode="json"), adw_id, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
