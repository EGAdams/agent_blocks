"""IToast: a Windows toast from inside WSL. The same PowerShell dashboard_watch.sh uses, with the title and
message passed by environment (WSLENV), never spliced into the script."""
from __future__ import annotations

import os

from ..models import Alert
from .runtime import child_env, run

POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
SCRIPT = r'''
$ErrorActionPreference = "Stop"
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("ToastText02")
$n = $x.GetElementsByTagName("text")
[void]$n.Item(0).AppendChild($x.CreateTextNode($env:ALERT_TITLE))
[void]$n.Item(1).AppendChild($x.CreateTextNode($env:ALERT_MSG))
$app = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))
'''


class PowerShellToast:
    def __init__(self, powershell: str = POWERSHELL, timeout: int = 25) -> None:
        self._powershell, self._timeout = powershell, timeout

    def show(self, alert: Alert) -> str:
        share = ":".join(part for part in (os.environ.get("WSLENV", ""), "ALERT_TITLE", "ALERT_MSG") if part)
        env = child_env({"ALERT_TITLE": alert.title, "ALERT_MSG": alert.message, "WSLENV": share}, windows=True)
        ran = run([self._powershell, "-NoProfile", "-Command", SCRIPT], self._timeout, env=env, cwd="/mnt/c")
        if not ran.ok:
            raise RuntimeError(ran.said)
        return "toast shown"
