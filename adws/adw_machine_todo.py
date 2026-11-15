#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Todo -- give a machine's supervisor a todo, or work the todos in this machine's mailbox.

    adw_machine_todo.py --machine SQMC --text "Find out why scala-scanner restarted twice last night."
    adw_machine_todo.py --work                 take the queued todos, one agent run each (the watch starts this)
    adw_machine_todo.py --work --id <id>       only that one

Posting only files the todo in that supervisor's inbox and returns its id; nothing waits for an agent. The
result is read later with `adw_machine_mailbox.py read --machine M --id ID` (box `completed` or `failed`).
`--client` is who is asking: the Bridge worker passes the Bridge sender, and a machine accepts todos only from
the senders in its policy. The agent doing a todo works under the same rules and guard as in an incident.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402
import re                                                                # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a todo, or work this machine's queued todos.")
    parser.add_argument("--work", action="store_true")
    parser.add_argument("--id")
    parser.add_argument("--machine")
    parser.add_argument("--text")
    parser.add_argument("--client", default="operator")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    if not args.work and not (args.machine and args.text and args.text.strip()):
        parser.error("give --work, or both --machine and --text")
    if not re.match(r"^[a-z][a-z0-9-]{0,60}$", args.client) or len(args.text or "") > 4000:
        parser.error("--client must be a sender name, and --text at most 4000 characters")
    adw_id, kit = start("todo --work" if args.work else f"todo for {args.machine}")
    if args.work:
        lines = kit.todo_worker.work(args.id)
        return finish(args.json_out, True, f"{len(lines)} todo(s) touched", {"lines": lines}, adw_id)
    ok, line, data = kit.todo_poster.post(args.machine, args.text.strip(), args.client, args.task_id)
    kit.log.line(f"todo: {line}")
    return finish(args.json_out, ok, line, data, adw_id)


if __name__ == "__main__":
    sys.exit(main())
