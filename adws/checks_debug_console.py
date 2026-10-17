#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Reproducible checks for the debug console's action layer.

    uv run adws/checks_debug_console.py

Not named adw_* on purpose: `ls adws/adw_*.py` is how the factory lists its
ADWs, and this is a check script, not a workflow. It drives DebugConsole
directly — the same object the menu drives — so it covers the sequence,
prerequisite handling and cleanup without a terminal.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

from adw_modules import agents
from adw_modules.data_types import ActionRequest
from adw_modules.debug_console import DebugConsole

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
failures: list[str] = []


def check(label: str, ok: bool, note: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + note if note else ''}")
    if not ok:
        failures.append(label)


def phase_rows(db: str, adw_id: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT name, status FROM phases WHERE adw_id = ? ORDER BY seq",
                            (adw_id,)).fetchall()
    finally:
        conn.close()


def main() -> int:
    cfg = agents.load_config(CONFIG)
    db = cfg.observability.db
    tmp = Path(tempfile.mkdtemp(prefix="sssf_console_check_"))
    first, second = tmp / "first.txt", tmp / "second.txt"
    first.write_text("one\ntwo\nthree\n")
    second.write_text("only line\n")

    print("\n1. out-of-order selections are not_run, and open no phase")
    console = DebugConsole(cfg, None)
    blank = console.adw_id
    check("read before open -> not_run",
          console.invoke(ActionRequest(action_id="read")).outcome == "not_run")
    check("print before read -> not_run",
          console.invoke(ActionRequest(action_id="print")).outcome == "not_run")
    check("no phase recorded for a not_run", phase_rows(db, blank) == [],
          f"rows={phase_rows(db, blank)}")

    print("\n2. open -> read -> print, one phase each")
    opened = console.invoke(ActionRequest(action_id="open", path=str(first)))
    check("open succeeded", opened.outcome == "success", opened.error)
    check("open read nothing", console.state().has_text is False)
    read = console.invoke(ActionRequest(action_id="read"))
    check("read succeeded", read.outcome == "success", read.error)
    check("read reported 14 bytes / 3 lines",
          (console.state().byte_count, console.state().line_count) == (14, 3))
    printed = console.invoke(ActionRequest(action_id="print"))
    check("print succeeded", printed.outcome == "success", printed.error)
    check("print emitted the 3 read lines",
          [line for line in printed.detail if line in ("one", "two", "three")]
          == ["one", "two", "three"])
    check("three phases traced",
          phase_rows(db, console.adw_id)
          == [("open_file", "success"), ("read_file", "success"), ("print_file", "success")],
          f"rows={phase_rows(db, console.adw_id)}")

    print("\n3. selecting a different file invalidates the old contents")
    console.invoke(ActionRequest(action_id="open", path=str(second)))
    check("contents cleared by the new open", console.state().has_text is False)
    check("print now not_run, not stale text",
          console.invoke(ActionRequest(action_id="print")).outcome == "not_run")
    console.invoke(ActionRequest(action_id="read"))
    again = console.invoke(ActionRequest(action_id="print"))
    check("print shows the NEW file", "only line" in again.detail)
    check("print does not show the OLD file", "one" not in again.detail)

    print("\n4. a missing file fails the phase, and the menu's object survives")
    before = console.adw_id
    bad = console.invoke(ActionRequest(action_id="open", path=str(tmp / "nope.txt")))
    check("missing file -> fail", bad.outcome == "fail", bad.summary)
    check("error names the file", "nope.txt" in bad.error, bad.error)
    check("the failed phase is recorded as fail",
          ("open_file", "fail") in phase_rows(db, before))
    check("the console is still usable after a failure",
          console.invoke(ActionRequest(action_id="state")).outcome == "success")
    check("a failed open left the previous state untouched",
          console.state().handle_open is True
          and console.state().selected_path == str(second))

    print("\n5. reset releases the handle and starts a clean session")
    console.invoke(ActionRequest(action_id="open", path=str(first)))
    check("handle open before reset", console.state().handle_open is True)
    old = console.adw_id
    console.invoke(ActionRequest(action_id="reset"))
    check("handle released by reset", console.state().handle_open is False)
    check("contents cleared by reset", console.state().has_text is False)
    check("a new adw_id is in play", console.adw_id != old, f"{old} -> {console.adw_id}")

    print("\n6. the whole-ADW action runs all three phases in one selection")
    whole = console.invoke(ActionRequest(action_id="adw", path=str(first)))
    check("whole ADW succeeded", whole.outcome == "success", whole.error)
    check("three phases from one selection",
          phase_rows(db, console.adw_id)
          == [("open_file", "success"), ("read_file", "success"), ("print_file", "success")],
          f"rows={phase_rows(db, console.adw_id)}")

    print("\n7. logs come from SSSF's own per-session trace")
    logs = console.invoke(ActionRequest(action_id="logs"))
    check("events.jsonl exists and is tailed", console.events_path.exists())
    check("log lines were returned", len(logs.detail) > 1)

    print("\n8. new session / clear state are separable")
    console.invoke(ActionRequest(action_id="open", path=str(first)))
    console.invoke(ActionRequest(action_id="read"))
    before = console.adw_id
    carried = console.invoke(ActionRequest(action_id="new_session"))
    check("new session mints a new adw_id", console.adw_id != before,
          f"{before} -> {console.adw_id}")
    check("the closed session is named in the result",
          any(before in line for line in carried.detail), str(carried.detail))
    check("new session CARRIES the open file", console.state().handle_open is True)
    check("new session carries the contents too", console.state().has_text is True)
    check("print still works in the new session, no re-open needed",
          console.invoke(ActionRequest(action_id="print")).outcome == "success")
    check("the carried phase lands under the NEW adw_id",
          phase_rows(db, console.adw_id) == [("print_file", "success")],
          f"rows={phase_rows(db, console.adw_id)}")
    # `before` also carries group 6's whole-ADW phases, so only the tail is ours:
    # the point is that the phases run BEFORE the switch stayed behind it.
    check("the old session kept its own phases",
          [name for name, _ in phase_rows(db, before)][-2:] == ["open_file", "read_file"],
          f"rows={phase_rows(db, before)}")

    same = console.adw_id
    cleared = console.invoke(ActionRequest(action_id="clear_state"))
    check("clear state KEEPS the adw_id", console.adw_id == same, cleared.summary)
    check("clear state releases the handle", console.state().handle_open is False)
    check("clear state forgets the contents", console.state().has_text is False)
    check("clear state forgets the selection", console.state().selected_path == "")
    check("read is not_run again after clearing",
          console.invoke(ActionRequest(action_id="read")).outcome == "not_run")
    check("clearing opened no phase of its own",
          [name for name, _ in phase_rows(db, same)] == ["print_file"],
          f"rows={phase_rows(db, same)}")

    print("\n9. close() releases resources and settles the run")
    console.invoke(ActionRequest(action_id="open", path=str(first)))
    handle = console.demo.handle
    console.close()
    check("handle closed on exit", handle.closed is True)

    print(f"\n{'all checks passed' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
