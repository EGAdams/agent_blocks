"""What every Machine Supervisor ADW shares: an id, the kit, and one way to finish.

Each `adws/adw_machine_<op>.py` is a thin file over this. None of them takes a path or a shell line from its
caller, and none of them needs a model to start: the cron ones never reach one at all.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from .kit import Kit


def start(name: str, quiet: bool = False) -> tuple[str, Kit]:
    """`quiet` for the ADWs whose output is read by a program: only their answer goes to stdout."""
    adw_id = uuid.uuid4().hex[:8]
    if not quiet:
        print(f"adw_id: {adw_id}", flush=True)
    kit = Kit()
    if quiet:
        kit.log.quiet()
    kit.log.line(f"[{adw_id}] {name} on {kit.catalog.me().name}")
    return adw_id, kit


def finish(json_out: str | None, ok: bool, line: str, data: dict | None = None, adw_id: str = "",
           verified: bool | None = None, as_json: bool = False) -> int:
    """Print the one line (or, with `as_json`, the whole answer as the last line), write --json-out, and
    return the exit status: 0 done, 1 not done."""
    answer = {"ok": ok, "verified": ok if verified is None else verified, "line": line, "data": data or {},
              "adw_id": adw_id, "retryable": False}
    if json_out:
        path = Path(json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(answer, indent=2, default=str), encoding="utf-8")
    print(json.dumps(answer, default=str) if as_json else line, flush=True)
    return 0 if ok else 1
