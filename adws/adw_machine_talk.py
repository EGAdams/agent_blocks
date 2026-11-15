#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Talk -- ask another machine's supervisor a question and wait for the answer. Real time, over SSH.

    adw_machine_talk.py --to SQMC --message "Is dashboard-server running there, and what do its last 20 log lines say?"

A supervisor agent runs this from its own Bash tool while it works on an incident. The other supervisor is
woken, looks (it changes nothing), and its answer is printed. When MACHINE_SUPERVISOR_INCIDENT names an
incident this machine holds, the incident travels with the question. The supervisor who answers cannot ask
anyone in turn (MACHINE_SUPERVISOR_NO_TALK), so two of them can never talk in a circle.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402
import os                                                                # noqa: E402

from agent_comms.models import TaskStatus                                # noqa: E402
from machine_supervisor.errors import Undelivered                        # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask another supervisor a question.")
    parser.add_argument("--to", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--json-out")
    args = parser.parse_args()
    if os.environ.get("MACHINE_SUPERVISOR_NO_TALK") == "1":
        print("You are answering another supervisor's question, so you cannot ask one. Answer from what you can see.")
        return 2
    adw_id, kit = start(f"talk to {args.to}")
    context = {}
    try:
        context["incident"] = kit.incidents.get(os.environ.get("MACHINE_SUPERVISOR_INCIDENT", "")).model_dump(mode="json")
    except (KeyError, ValueError):
        pass
    try:
        to = kit.catalog.get(args.to)
        if to.name == kit.catalog.me().name:
            return finish(args.json_out, False, "That is this machine. Look for yourself.", adw_id=adw_id)
        reply = kit.courier.send(to, "ask", args.message, context, kit.catalog.policy().ask_timeout + 60, priority=30)
    except KeyError as error:
        return finish(args.json_out, False, str(error.args[0]), adw_id=adw_id)
    except Undelivered as error:
        return finish(args.json_out, False, f"{args.to} did not answer: {error}", adw_id=adw_id)
    if reply.outcome is not TaskStatus.SUCCEEDED:
        return finish(args.json_out, False, f"{to.name} could not answer: {reply.error or 'no reason given'}", adw_id=adw_id)
    return finish(args.json_out, True, f"{to.name} answers:\n{reply.result}", {"answer": reply.result}, adw_id)


if __name__ == "__main__":
    sys.exit(main())
