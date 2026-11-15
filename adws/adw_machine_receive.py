#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Receive -- take one handoff addressed to this machine's supervisor and answer it.

    ssh <this machine> <python> adws/adw_machine_receive.py < envelope.json

The handoff (an agent_comms Envelope, Swarm-Forge's handoff file) arrives on stdin and is filed in inbox/new
before anything else happens. `alert`, `help`, `ask` and `mail` come only from another supervisor and are
answered in this run; a `todo` is filed, the worker is started, and the reply says queued. The reply is the
last line printed, as JSON. Exit status: 0 done or queued, 1 the handoff failed, 2 it was not a handoff for us.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402
from agent_comms.models import TaskStatus                                # noqa: E402
from machine_supervisor.errors import Refused                            # noqa: E402


def main() -> int:
    argparse.ArgumentParser(description="Receive one handoff from stdin.").parse_args()
    _, kit = start("receive", quiet=True)
    try:
        reply = kit.receiver.receive(sys.stdin.read())
    except Refused as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(reply.model_dump_json(), flush=True)
    return 0 if reply.outcome in (TaskStatus.SUCCEEDED, TaskStatus.QUEUED) else 1


if __name__ == "__main__":
    sys.exit(main())
