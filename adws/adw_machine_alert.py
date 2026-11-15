#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Alert -- show one Windows toast on every machine: here directly, the others over the handoff channel.

    adw_machine_alert.py --title "Dashboard is down" --message "SQMC stopped answering at 14:02."

One line per machine says whether it was shown. A machine that is off is reported, not retried.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402
from machine_supervisor.models import Alert                              # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Toast every machine.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--json-out")
    args = parser.parse_args()
    adw_id, kit = start("alert")
    lines = kit.alerter.alert(Alert(title=args.title[:120], message=args.message[:600]))
    for line in lines:
        kit.log.line(line)
    shown = [line for line in lines if "NOT shown" not in line]
    return finish(args.json_out, bool(shown), f"alert shown on {len(shown)} of {len(lines)} machines",
                  {"deliveries": lines}, adw_id)


if __name__ == "__main__":
    sys.exit(main())
