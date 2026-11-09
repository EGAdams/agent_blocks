"""The Windows side's scripts and parsing, with PowerShell faked. Nothing runs on Windows."""
from __future__ import annotations

import base64
import re

import pytest

from browser_control.models import BROWSERS
from browser_control.windows import InteractiveTaskStarter, RetryingPowerShell, WindowsSessionProbe, windows_path

CHROME = BROWSERS["chrome"]


class FakePowerShell:
    def __init__(self, code=0, out=""): self.code, self.out, self.scripts = code, out, []
    def __call__(self, script): self.scripts.append(script); return self.code, self.out


def test_windows_path():
    assert windows_path("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe") == \
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    with pytest.raises(ValueError):
        windows_path("/usr/bin/chrome")


def test_the_launch_is_an_interactive_task_and_the_url_cannot_change_the_script():
    ps = FakePowerShell(out="STARTED")
    url = "https://x.example/?q=a';Remove-Item C:\\ -Recurse;'\"b"
    assert InteractiveTaskStarter(ps, exists=lambda p: True).start(CHROME, url) == (True, "")
    script = ps.scripts[0]
    assert "-LogonType Interactive" in script and "--remote-debugging-port=9222" in script
    assert "Remove-Item" not in script                    # the URL is only ever inside base64
    blob = re.search(r"FromBase64String\('([^']+)'\)", script).group(1)
    assert base64.b64decode(blob).decode() == url.replace('"', "%22")


def test_the_launch_reports_no_desktop_and_a_failed_task():
    assert "nobody is signed in" in InteractiveTaskStarter(FakePowerShell(3, "NO_DESKTOP"), exists=lambda p: True).start(CHROME, "about:blank")[1]
    ok, why = InteractiveTaskStarter(FakePowerShell(1, "Access is denied"), exists=lambda p: True).start(CHROME, "about:blank")
    assert not ok and "Access is denied" in why
    assert not InteractiveTaskStarter(FakePowerShell(), exists=lambda p: False).start(CHROME, "about:blank")[0]


def test_placement_parsing():
    p = WindowsSessionProbe(FakePowerShell(out='{"found":true,"pid":17488,"session":1,"desktops":[1]}')).placement(CHROME)
    assert p.known and p.visible and p.pid == 17488
    hidden = WindowsSessionProbe(FakePowerShell(out='{"found":true,"pid":15160,"session":0,"desktops":1}')).placement(CHROME)
    assert hidden.known and not hidden.visible and hidden.desktops == [1]
    broken = WindowsSessionProbe(FakePowerShell(-1, "TimeoutExpired")).placement(CHROME)
    assert not broken.known and "TimeoutExpired" in broken.reason


STALL = (1, "<3>WSL (1845265 - ) ERROR: UtilAcceptVsock:281: accept4 failed 110")


class ScriptedPowerShell:
    def __init__(self, *answers): self.answers, self.calls = list(answers), 0
    def __call__(self, script): self.calls += 1; return self.answers.pop(0)


def test_an_interop_stall_is_tried_again_and_the_placement_still_read():
    ps = ScriptedPowerShell(STALL, STALL, (0, '{"found":true,"pid":17488,"session":1,"desktops":[1]}'))
    p = WindowsSessionProbe(RetryingPowerShell(ps)).placement(CHROME)
    assert ps.calls == 3 and p.known and p.found and p.session == 1


def test_only_a_stall_is_retried_and_only_so_often():
    real_failure = ScriptedPowerShell((1, "Get-CimInstance : Access denied"))
    assert RetryingPowerShell(real_failure)("x") == (1, "Get-CimInstance : Access denied") and real_failure.calls == 1
    stuck = ScriptedPowerShell(STALL, STALL, STALL, (0, "never reached"))
    assert RetryingPowerShell(stuck, attempts=3)("x") == STALL and stuck.calls == 3


def test_the_launch_is_never_retried():
    ps = ScriptedPowerShell(STALL, (0, "STARTED"))
    ok, why = InteractiveTaskStarter(ps, exists=lambda p: True).start(CHROME, "about:blank")
    assert not ok and ps.calls == 1, "a stalled launch is reported, not repeated"
