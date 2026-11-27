#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Agent Task -- run one real agent task on this machine, with no Bridge queue, and watch it.

The probe agent is a real Claude CLI session, read-only (Read, Glob, Grep), in a directory you name. Its task
goes through the post office's mailboxes like any other, so the live terminal shows each step as it happens;
this run copies the same steps into its own SSSF session, which the Object Debugger reads. `--check` commands
run afterwards with their output streamed. An `implement` probe is judged like any write task, so one that
reports BLOCKED ends failed.

    lancedb_memory/.venv/bin/python adws/adw_agent_task.py --workdir DIR --prompt TEXT
        [--operation explain|implement] [--check "CMD ..."]... [--model haiku]
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "lancedb_memory")]

from live_activity.agent_run import run_probe  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_probe(sys.argv[1:]))
