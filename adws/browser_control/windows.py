"""The Windows side of Browser Control: start Chrome on the person's desktop, and say which desktop it is on.

Why a scheduled task. A program started through WSL interop opens in the Windows session of the `wslhost`
that carries the interop socket. When WSL was (re)started remotely -- over ssh, by a service -- that session
is 0, the hidden services desktop: Chrome runs, answers its DevTools port, passes every check, and no person
can ever see it. An "Interactive only" scheduled task run as the signed-in user always opens on that user's
desktop, whichever session WSL happens to be in. `StartWSLDashboard` on the live box already works this way.

Why the session probe. "The port answers" proves Chrome runs, not that anyone can see it. The probe asks
Windows which session the Chrome owning the debug port lives in, and which sessions have a desktop
(explorer.exe) -- the visibility phase compares the two.

Every script goes to PowerShell as -EncodedCommand, and the one value that comes from a caller (the URL)
travels inside it base64-encoded, so no quoting of it can change what the script does.
"""
from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Callable
from pathlib import PurePosixPath

from .models import BrowserKind, BrowserPlacement

POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
TASK_PATH, TASK_NAME = "\\ADW\\", "ADW_OpenChrome"

#: (script) -> (exit code, stdout). Raises nothing.
PowerShell = Callable[[str], tuple[int, str]]


def run_powershell(script: str, timeout: float = 45.0) -> tuple[int, str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                              capture_output=True, text=True, timeout=timeout, cwd="/mnt/c")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return done.returncode, (done.stdout or "").strip() or (done.stderr or "").strip()[:300]


def windows_path(wsl_path: str) -> str:
    """/mnt/c/Program Files/x.exe -> C:\\Program Files\\x.exe"""
    parts = PurePosixPath(wsl_path).parts
    if len(parts) < 3 or parts[:2] != ("/", "mnt") or len(parts[2]) != 1:
        raise ValueError(f"not a /mnt/<drive>/ path: {wsl_path!r}")
    return f"{parts[2].upper()}:\\" + "\\".join(parts[3:])


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode()


LAUNCH_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$desk = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | Where-Object { $_.SessionId -ne 0 })
if ($desk.Count -eq 0) { 'NO_DESKTOP'; exit 3 }
$url = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__URL__'))
$dir = Join-Path $env:LOCALAPPDATA '__PROFILE__'
$argv = '--remote-debugging-port=__PORT__ --user-data-dir="' + $dir + '" --no-first-run --new-window "' + $url + '"'
$a = New-ScheduledTaskAction -Execute '__EXE__' -Argument $argv
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
Register-ScheduledTask -TaskName '__TASK__' -TaskPath '__TASKPATH__' -Action $a -Principal $p -Settings $s -Force | Out-Null
Start-ScheduledTask -TaskName '__TASK__' -TaskPath '__TASKPATH__'
'STARTED'
"""

PLACEMENT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$c = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
     Where-Object { $_.CommandLine -like '*--remote-debugging-port=__PORT__*' -and $_.CommandLine -notlike '*--type=*' } |
     Select-Object -First 1
$d = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object { [int]$_.SessionId } | Sort-Object -Unique)
@{ found = [bool]$c; pid = $(if ($c) { [int]$c.ProcessId } else { 0 }); session = $(if ($c) { [int]$c.SessionId } else { -1 }); desktops = $d } | ConvertTo-Json -Compress
"""


class InteractiveTaskStarter:
    """IBrowserStarter: Chrome on the signed-in person's desktop, through an Interactive-only scheduled task."""

    def __init__(self, powershell: PowerShell = run_powershell, exists: Callable[[str], bool] | None = None) -> None:
        from pathlib import Path
        self._ps = powershell
        self._exists = exists or (lambda p: Path(p).exists())

    def start(self, kind: BrowserKind, url: str) -> tuple[bool, str]:
        if not self._exists(kind.exe):
            return False, f"{kind.name} is not installed where expected ({kind.exe})"
        script = (LAUNCH_SCRIPT.replace("__URL__", _b64(url.replace('"', "%22"))).replace("__PROFILE__", kind.profile)
                  .replace("__PORT__", str(int(kind.port))).replace("__EXE__", windows_path(kind.exe))
                  .replace("__TASKPATH__", TASK_PATH).replace("__TASK__", TASK_NAME))
        code, out = self._ps(script)
        if "NO_DESKTOP" in out:
            return False, "nobody is signed in to the Windows desktop, so there is nowhere visible to open it"
        if code != 0 or "STARTED" not in out:
            return False, f"the Windows scheduled task that opens it did not start ({out[:200] or f'exit {code}'})"
        return True, ""


class WindowsSessionProbe:
    """IWindowSessions: which Windows session holds the Chrome on this debug port, and which sessions have a desktop."""

    def __init__(self, powershell: PowerShell = run_powershell) -> None:
        self._ps = powershell

    def placement(self, kind: BrowserKind) -> BrowserPlacement:
        code, out = self._ps(PLACEMENT_SCRIPT.replace("__PORT__", str(int(kind.port))))
        try:
            data = json.loads(out.splitlines()[-1]) if code == 0 and out else None
        except (ValueError, IndexError):
            data = None
        if not isinstance(data, dict):
            return BrowserPlacement(known=False, reason=f"Windows did not say where {kind.name} is ({out[:160] or f'exit {code}'})")
        desktops = data.get("desktops") or []
        desktops = [int(d) for d in (desktops if isinstance(desktops, list) else [desktops])]
        return BrowserPlacement(known=True, found=bool(data.get("found")), pid=int(data.get("pid") or 0),
                                session=int(data.get("session", -1)), desktops=desktops)
