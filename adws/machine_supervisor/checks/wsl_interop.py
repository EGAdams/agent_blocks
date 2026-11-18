"""Command check: can this WSL reliably launch a Windows program? Exit 0 = yes.

Run by `adw_machine_watch.py` as a `command` check (cron, no model, stdlib only).

Why it exists: on 2026-11-17 every scan on SQMC timed out while all fifteen of
its checks passed. WSL's Windows side was dropping interop calls at random
(`UtilAcceptVsock: accept4 failed 110`), so the dashboard's `powershell.exe`
scanner scripts hung. Each socket in /run/WSL answered some calls in 0.1 s
and hung on others, so "some socket works once" is not health.

What it does, the way the dashboard's `hardware/wsl_interop.py` does it:
find a `<pid>_interop` socket that relays (newest first), then make
`CONFIRM` more calls through it. A healthy WSL answers every one. Cron
inherits no WSL_INTEROP, so each socket is tried by name.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

CMD_EXE = "/mnt/c/Windows/System32/cmd.exe"
RUN_DIR = "/run/WSL"
CALL_TIMEOUT = 4        # a healthy call takes ~0.1 s; a dropped one hangs until killed
CONFIRM = 3             # further calls that must all succeed through the socket found
BUDGET = 45             # seconds; stays inside the check's own timeout


def sockets() -> list[str]:
    found: list[tuple[float, str]] = []
    try:
        names = os.listdir(RUN_DIR)
    except OSError:
        return []
    for name in names:
        path = os.path.join(RUN_DIR, name)
        if not name.endswith("_interop") or os.path.islink(path) or not os.path.exists(path):
            continue
        try:
            found.append((os.path.getmtime(path), path))
        except OSError:
            continue
    return [path for _, path in sorted(found, reverse=True)]


def relays(sock: str) -> bool:
    try:
        done = subprocess.run([CMD_EXE, "/c", "exit"], env={"PATH": "/usr/bin:/bin", "WSL_INTEROP": sock},
                              capture_output=True, timeout=CALL_TIMEOUT)
        return done.returncode == 0
    except Exception:                                   # a timeout or a missing cmd.exe is "no"
        return False


def main() -> int:
    if not os.path.exists(CMD_EXE):
        print(f"FAIL: {CMD_EXE} is not visible; the Windows drive is not mounted in WSL")
        return 1
    candidates = sockets()
    if not candidates:
        print(f"FAIL: no interop sockets in {RUN_DIR}; no WSL session can reach Windows")
        return 1
    started = time.monotonic()
    tried = 0
    for sock in candidates:
        if time.monotonic() - started > BUDGET:
            break
        tried += 1
        if not relays(sock):
            continue
        for n in range(1, CONFIRM + 1):
            if not relays(sock):
                print(f"FAIL: {os.path.basename(sock)} relayed once, then call {n + 1} of {CONFIRM + 1} hung "
                      f"-- WSL's Windows side is dropping interop calls")
                return 1
        print(f"OK: {os.path.basename(sock)} relayed {CONFIRM + 1} of {CONFIRM + 1} calls "
              f"({tried} of {len(candidates)} sockets tried)")
        return 0
    print(f"FAIL: none of {tried} interop sockets (of {len(candidates)}) could launch cmd.exe")
    return 1


if __name__ == "__main__":
    sys.exit(main())
