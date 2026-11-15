#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Supervise -- wake the supervisor agent for one incident and climb the ladder.

    adw_machine_supervise.py --incident 20261114T120000Z-bati-ab12cd
    adw_machine_supervise.py --drill      a made-up incident nobody can fix, so every rung, alert and mail runs

Rung 1 is this machine's agent, rungs 2 and 3 the other two machines' agents, woken over the handoff channel.
After each rung the checks are run again from here; that, not the agent's word, decides whether it is fixed.
Fixed: a mail saying what was wrong and what was done. Not fixed after rung 1: a toast on every machine. Not
fixed after rung 3: a HIGH PRIORITY mail carrying a prompt for a more capable agent, and the check is left
alone for the cooldown. The watch starts this detached; it is safe to run by hand.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402
import re                                                                # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Wake the supervisor for one incident.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--incident")
    which.add_argument("--drill", action="store_true")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    if args.incident and not re.match(r"^[0-9A-Za-z-]{1,80}$", args.incident):
        print("not an incident id", file=sys.stderr)
        return 2
    adw_id, kit = start("supervise " + ("--drill" if args.drill else args.incident))
    try:
        incident = kit.escalation.drill() if args.drill else kit.escalation.run(args.incident)
    except KeyError:
        return finish(args.json_out, False, f"no incident {args.incident} on this machine", adw_id=adw_id)
    if incident is None:
        return finish(args.json_out, False, "another supervisor run holds the lock; nothing done", adw_id=adw_id)
    line = f"incident {incident.id}: {incident.status} after {len(incident.attempts)} attempt(s)"
    return finish(args.json_out, incident.status == "fixed", line, incident.model_dump(mode="json"), adw_id)


if __name__ == "__main__":
    sys.exit(main())
