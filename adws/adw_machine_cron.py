#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Cron -- run one cron job, and wake the supervisor if it fails. No model unless it fails.

    */30 * * * * <python> adws/adw_machine_cron.py --job backup-notes -- /home/adamsl/bin/backup_notes.sh

The command runs as given, its output passed through. Exit 0: nothing else happens. Any other exit: the
failure is reported to the watch as check `cron-<job>`, which wakes the supervisor under the usual rules (one
at a time, a daily limit, a cooldown after an unresolved incident). Wrap only jobs that are safe to run twice:
after an agent's attempt the command is run again to see whether it now passes.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402
import re                                                                # noqa: E402
import subprocess                                                        # noqa: E402

from machine_supervisor.models import Check, CheckResult                 # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one cron job; wake the supervisor if it fails.")
    parser.add_argument("--job", required=True)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,40}$", args.job) or not command:
        parser.error("--job is lower-case letters, digits and dashes, and a command must follow --")
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", f"timed out after {args.timeout}s"
    except OSError as error:
        code, out, err = 127, "", str(error)
    sys.stdout.write(out)
    sys.stderr.write(err)
    if code == 0:
        return 0
    adw_id, kit = start(f"cron {args.job} failed")
    tail = " | ".join((err.strip() or out.strip()).splitlines()[-3:])[:300]
    name = f"cron-{args.job}"
    check = Check(name=name, kind="command", argv=command, threshold=1, timeout=min(args.timeout, 600),
                  about=f"A cron job on this machine, run through adw_machine_cron.py. It exited {code}. "
                        "Running it again is the test of whether it is fixed.")
    outcome = kit.watch.report(check, CheckResult(check=name, ok=False, detail=f"exit {code}: {tail}"))
    finish(None, False, "; ".join(outcome.lines), adw_id=adw_id)
    return code


if __name__ == "__main__":
    sys.exit(main())
