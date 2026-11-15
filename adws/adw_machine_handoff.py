#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Handoff -- send one handoff to another machine's supervisor and wait for the reply.

    adw_machine_handoff.py --to SQMC --type alert --text "body" --context '{"title": "Heads up"}'

The general form of the channel; `adw_machine_talk.py`, `adw_machine_alert.py` and `adw_machine_todo.py` are
the everyday ones. The handoff is written to this machine's outbox, carried over SSH to the other machine's
`adw_machine_receive.py`, and moved to sent or failed; the reply is filed in the inbox.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402
import json                                                              # noqa: E402

from agent_comms.models import TaskStatus                                # noqa: E402
from machine_supervisor.errors import Undelivered                        # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one handoff and wait for the reply.")
    parser.add_argument("--to", required=True)
    parser.add_argument("--type", required=True, choices=["alert", "help", "ask", "mail", "todo"])
    parser.add_argument("--text", required=True)
    parser.add_argument("--context", default="{}")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--priority", type=int, default=50, choices=range(0, 100), metavar="0-99")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    try:
        context = json.loads(args.context)
        assert isinstance(context, dict)
    except (ValueError, AssertionError):
        print("--context must be a JSON object", file=sys.stderr)
        return 2
    adw_id, kit = start(f"handoff {args.type} to {args.to}")
    try:
        reply = kit.courier.send(kit.catalog.get(args.to), args.type, args.text, context, args.timeout, args.priority)
    except KeyError as error:
        return finish(args.json_out, False, str(error.args[0]), adw_id=adw_id)
    except Undelivered as error:
        return finish(args.json_out, False, f"not delivered: {error}", adw_id=adw_id)
    ok = reply.outcome in (TaskStatus.SUCCEEDED, TaskStatus.QUEUED)
    line = f"{reply.sender}: {reply.outcome.value if reply.outcome else '?'} -- {(reply.result or reply.error or '')[:300]}"
    return finish(args.json_out, ok, line, {"reply": json.loads(reply.model_dump_json())}, adw_id)


if __name__ == "__main__":
    sys.exit(main())
