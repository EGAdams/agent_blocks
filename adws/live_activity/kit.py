"""The only composition root: this machine's sources, or another machine's ADW over SSH."""
from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .agent_source import PostOfficeEventSource
from .models import Activity
from .supervisor_source import FolderSupervisorSource
from .text import iso, safe

REPO = Path(__file__).resolve().parents[2]
POST_OFFICE = REPO / "adws" / "adw_data" / "post_office"
SUPERVISOR_ROOT = Path.home() / ".local" / "share" / "machine_supervisor"
MACHINES = REPO / "adws" / "machine_supervisor" / "machines.json"


def machines(path: Path = MACHINES) -> list[dict]:
    return json.loads(Path(os.environ.get("MACHINE_SUPERVISOR_MACHINES") or path).read_text())["machines"]


def this_machine(table: list[dict]) -> str:
    host = socket.gethostname().lower()
    return next((m["name"] for m in table if m["hostname"].lower() == host), socket.gethostname())


def local(after: int | None, name: str, post_office: Path = POST_OFFICE, root: Path | None = None) -> Activity:
    root = root or Path(os.environ.get("MACHINE_SUPERVISOR_ROOT") or SUPERVISOR_ROOT)
    agent_events, cursor = PostOfficeEventSource(post_office, name).read(after)
    supervisor_events, gaps = FolderSupervisorSource(root, name).read()
    return Activity(machine=name, server_now=iso(datetime.now(timezone.utc)), cursor=cursor,
                    agent_events=agent_events, supervisor_events=supervisor_events, gaps=gaps)


def remote(after: int | None, target: dict, run=subprocess.run) -> Activity:
    name, ssh = target["name"], target.get("ssh") or {}
    command = [target["python"], f"{target['repo']}/adws/adw_live_activity.py"]
    if after is not None:
        command += ["--after", str(int(after))]
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-p", str(ssh.get("port", 22)),
            f"{ssh.get('user', 'adamsl')}@{ssh.get('host', '')}", shlex.join(command)]
    now = iso(datetime.now(timezone.utc))
    try:
        done = run(argv, capture_output=True, text=True, timeout=20)
        if done.returncode != 0:
            raise RuntimeError(safe((done.stderr or "").strip().splitlines()[-1:] or f"exit {done.returncode}"))
        return Activity.model_validate_json(done.stdout)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        why = safe(f"{type(error).__name__}: {error}", 300)
        return Activity(ok=False, error=why, machine=name, server_now=now, cursor=after or 0,
                        gaps=[f"{name}: could not be read ({why})"])


def read(after: int | None, machine: str | None = None) -> Activity:
    table = machines()
    me = this_machine(table)
    if not machine or machine.upper() == me.upper():
        return local(after, me)
    target = next((m for m in table if m["name"] == machine.upper()), None)
    if target is None:
        return Activity(ok=False, error=f"no machine called {safe(machine, 40)}", machine=machine[:40],
                        server_now=iso(datetime.now(timezone.utc)),
                        gaps=[f"{safe(machine, 40)}: not enrolled in machines.json"])
    return remote(after, target)
