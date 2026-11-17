"""What every Android Photos ADW shares: an id, the kit, one request log and one way to finish.

`run(operation, argv)` is the whole workflow; each `adws/adw_android_<op>.py` is a thin file naming its
operation. None of them takes a path, a device name or a shell line from its caller: the device comes from the
allowlist, the folders from settings, and a photograph is named by its number or its file name.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from .kit import Kit
from .models import DEFAULT_COUNT, MAX_COUNT
from .operations import NotReady

OPERATIONS = ("status", "list_recent_photos", "get_photo", "get_thumbnail")


def finish(json_out: str | None, ok: bool, line: str, data: dict, adw_id: str, as_json: bool) -> int:
    answer = {"ok": ok, "verified": ok, "line": line, "data": data, "adw_id": adw_id, "retryable": False}
    if json_out:
        path = Path(json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(answer, indent=2, default=str), encoding="utf-8")
    shown = {**answer, "data": {k: v for k, v in data.items() if k != "files"}}
    print(json.dumps(shown, default=str) if as_json else line, flush=True)
    return 0 if ok else 1


def run(operation: str | None = None, argv: list[str] | None = None, kit: Kit | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only access to the photos on EG's USB-connected Android phone.")
    if operation is None:
        parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"how many photos, 1-{MAX_COUNT}")
    parser.add_argument("--number", type=int, help="a photo's number in the last listing (1 = newest)")
    parser.add_argument("--name", help="a photo's file name")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--json-out")
    parser.add_argument("--task-id", default="")                 # the Bridge's bookkeeping, kept in the request log
    parser.add_argument("--client", default="local")
    args = parser.parse_args(argv)
    operation = operation or args.operation
    adw_id = uuid.uuid4().hex[:8]
    if not args.json:
        print(f"adw_id: {adw_id}", flush=True)
    kit = kit or Kit()
    kit.cache.purge()
    try:
        if operation == "status":
            status = kit.gate.status()
            ok, line = status.state == "ready", status.line
            data = {"android": operation, **status.model_dump()}
        elif operation == "list_recent_photos":
            answer = kit.recent.run(args.count)
            ok, line, data = answer.ok, answer.line, {"android": operation, **answer.model_dump()}
        else:
            if args.number is None and not args.name:
                return finish(args.json_out, False, "Say which photo: --number or --name.",
                              {"android": operation, "state": "bad_request"}, adw_id, args.json)
            answer = kit.one.run("full" if operation == "get_photo" else "preview", args.number, args.name)
            ok, line, data = answer.ok, answer.line, {"android": operation, **answer.model_dump()}
    except NotReady as not_ready:
        status = not_ready.status
        ok, line, data = False, status.line, {"android": operation, **status.model_dump()}
    if data.get("next_step") and not ok:
        line = f"{line} {data['next_step']}"
    sent = ", ".join(str(f.get("name")) for f in data.get("files") or []) or "-"
    with (kit.cache.root / "requests.log").open("a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [{adw_id}] {operation} client={args.client} "
                  f"task={args.task_id or '-'} state={data.get('state')} photos={sent}\n")
    return finish(args.json_out, ok, line, data, adw_id, args.json)
