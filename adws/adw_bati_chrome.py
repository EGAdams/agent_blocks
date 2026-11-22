#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW BATI Chrome -- drive the debug Chrome on BATI's interactive desktop, from BATI only. Read
bati_desktop/DESIGN.md first.

    adw_bati_chrome.py status
    adw_bati_chrome.py open --url http://localhost:8765/
    adw_bati_chrome.py show_view --view plan-for-today
    adw_bati_chrome.py read [--view plan-for-today]
    adw_bati_chrome.py screenshot
    adw_bati_chrome.py plan_for_today            # open -> show the Plan for Today -> read back -> screenshot

Every run: an ADW id, a `start` and an `end` line in adws/adw_data/bati_desktop/audit.jsonl, a hard deadline
(policy.operation_seconds), and on failure or timeout a toast on BATI's desktop plus an `alert` audit line.
Exit 0 only when the operation succeeded (for plan_for_today: verified by read-back).
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from bati_desktop.kit import build, now                                  # noqa: E402
from bati_desktop.models import BatiPolicy, Outcome                      # noqa: E402
from bati_desktop.operations import Refused                              # noqa: E402

OPERATIONS = ("status", "open", "show_view", "read", "screenshot", "plan_for_today")
DEFAULT_URL = "http://localhost:8765/"


class OperationTimeout(Exception):
    pass


def _on_alarm(signum: int, frame: object) -> None:
    raise OperationTimeout()


def run(op: str, args: argparse.Namespace, adw_id: str) -> Outcome:
    policy = BatiPolicy()
    started = now()
    kit = None
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(policy.operation_seconds)
    try:
        kit = build(policy)
        kit.audit.write({"adw_id": adw_id, "event": "start", "operation": op, "args": _args(args)})
        chrome, verified = kit.chrome, True
        if op == "status":
            data = chrome.status()
        elif op == "open":
            data = chrome.open(args.url)
        elif op == "show_view":
            data = chrome.show_view(args.view)
        elif op == "read":
            data = chrome.read(args.view or "")
        elif op == "screenshot":
            data = chrome.screenshot(adw_id)
        else:
            verified, data = chrome.plan_for_today(args.url, adw_id)
        message = f"{op} done on BATI" + ("" if verified else " but the read-back did not verify it")
        outcome = Outcome(adw_id=adw_id, operation=op, ok=verified, verified=verified, message=message,
                          cause="" if verified else "not_verified", data=data)
    except OperationTimeout:
        outcome = Outcome(adw_id=adw_id, operation=op, ok=False, cause="timeout",
                          message=f"{op} did not finish within {policy.operation_seconds}s")
    except Refused as stop:
        outcome = Outcome(adw_id=adw_id, operation=op, ok=False, cause=stop.cause, message=str(stop))
    except Exception as exc:                                        # every failure is an outcome, never a traceback
        outcome = Outcome(adw_id=adw_id, operation=op, ok=False, cause=type(exc).__name__, message=str(exc)[:400])
    finally:
        signal.alarm(0)
        if kit is not None:
            kit.relay.close()
    outcome.started, outcome.finished = started, now()
    _record(kit, outcome)
    return outcome


def _args(args: argparse.Namespace) -> dict[str, str]:
    return {k: str(v) for k, v in vars(args).items() if k in ("url", "view") and v}


def _record(kit: object, outcome: Outcome) -> None:
    from bati_desktop.kit import JsonlAudit, ToastAlerter
    audit = getattr(kit, "audit", None) or JsonlAudit()
    audit.write({"adw_id": outcome.adw_id, "event": "end", "operation": outcome.operation, "ok": outcome.ok,
                 "verified": outcome.verified, "cause": outcome.cause, "message": outcome.message})
    if not outcome.ok:
        alerter = getattr(kit, "alerter", None) or ToastAlerter()
        said = alerter.alert(f"BATI Chrome: {outcome.operation} failed",
                             f"{outcome.cause}: {outcome.message} (adw {outcome.adw_id})")
        audit.write({"adw_id": outcome.adw_id, "event": "alert", "operation": outcome.operation, "result": said})


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive the debug Chrome on BATI's desktop (BATI only).")
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--view", default="plan-for-today")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    adw_id = uuid.uuid4().hex[:8]
    t0 = time.monotonic()
    outcome = run(args.operation, args, adw_id)
    text = outcome.model_dump_json(indent=2)
    if args.json_out:
        Path(args.json_out).write_text(text)
    print(text)
    print(f"[adw {adw_id}] {outcome.operation}: {'OK' if outcome.ok else 'FAILED ' + outcome.cause} "
          f"in {time.monotonic() - t0:.1f}s", file=sys.stderr)
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
