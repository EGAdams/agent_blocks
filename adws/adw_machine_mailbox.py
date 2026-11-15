#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Mailbox -- read the supervisors' mailboxes and state. Reads only; this is the Bridge's window.

    adw_machine_mailbox.py machines
    adw_machine_mailbox.py status  --machine SQMC          its checks right now, todos queued, mail held
    adw_machine_mailbox.py mailbox --machine SQMC          how many messages are in each box
    adw_machine_mailbox.py mailbox --machine SQMC --box completed
    adw_machine_mailbox.py read    --machine SQMC --id 20261114T120000Z_ab12cd34_from_chatgpt-bridge

Boxes: new, in_process, completed, failed (the inbox), outbox, sent, undelivered. Another machine's answer
comes from running this same ADW there over SSH. `--machine` defaults to this machine.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a supervisor's mailbox or state.")
    parser.add_argument("operation", choices=["machines", "mailbox", "read", "status"])
    parser.add_argument("--machine")
    parser.add_argument("--box", default="all")
    parser.add_argument("--id", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--json-out")
    parser.add_argument("--task-id")                 # the Bridge's own bookkeeping; not used here
    parser.add_argument("--client")
    args = parser.parse_args()
    adw_id, kit = start(f"mailbox {args.operation}", quiet=args.json)
    machine = args.machine or kit.catalog.me().name
    try:
        if args.operation == "machines":
            ok, line, data = kit.mailboxes.machines()
        elif args.operation == "mailbox":
            ok, line, data = kit.mailboxes.mailbox(machine, args.box)
        elif args.operation == "read":
            ok, line, data = kit.mailboxes.read(machine, args.id)
        else:
            ok, line, data = kit.mailboxes.status(machine)
    except KeyError as error:
        ok, line, data = False, str(error.args[0]), {}
    return finish(args.json_out, ok, line, data, adw_id, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
