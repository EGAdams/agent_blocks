#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic"]
# ///
"""ADW Live Activity -- one read-only look at what agents and the supervisor are doing on a machine.

Prints one JSON object: agent events since `--after` (a byte cursor from the last answer) from the post
office's event log, and the supervisor's status, handoffs, incidents and held mail. `--machine NAME` reads
another machine's copy over SSH. It writes nothing, takes no lock and calls no model.

    uv run adws/adw_live_activity.py [--after N] [--machine BATI|SQMC|R46]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "lancedb_memory")]

from live_activity import kit  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--after", type=int)
    parser.add_argument("--machine")
    args = parser.parse_args(argv)
    print(kit.read(args.after, args.machine).model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
