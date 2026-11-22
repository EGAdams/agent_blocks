"""Offline tests: no Windows, no Chrome. Fakes stand in for the relay, the starter and the session probe."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ADWS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ADWS))

from browser_control.models import BROWSERS, BrowserPlacement          # noqa: E402
from bati_desktop.models import BatiPolicy                             # noqa: E402
from bati_desktop.operations import BatiChrome, Refused, bare, long_date   # noqa: E402
from bati_desktop.relay import RelayCdpConnector, RelayError, RelayTargetDirectory   # noqa: E402
from browser_session.models import DebugEndpoint                       # noqa: E402

DASH = "http://localhost:8765/"


class FakeRelay:
    """Answers like relay_win.py, over an in-memory Chrome whose page runs a scripted 'dashboard'."""

    def __init__(self, computer: str = "DESKTOP-SHDBATI", running: bool = True, tabs: list[str] | None = None,
                 heading: str = "Plan for Today — November 21, 2026", ignore_presses: int = 0) -> None:
        self.ignore_presses, self.presses = ignore_presses, 0
        self.computer, self.running, self.heading = computer, running, heading
        self.tabs = [{"id": f"T{i}", "type": "page", "url": u, "title": "Dashboard",
                      "webSocketDebuggerUrl": f"ws://127.0.0.1:9222/devtools/page/T{i}"}
                     for i, u in enumerate(tabs if tabs is not None else [DASH])]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.pressed = False
        self._pending: dict[int, str] = {}

    def request(self, op: str, timeout: float | None = None, **f: Any) -> dict[str, Any]:
        self.calls.append((op, f))
        if op == "hello":
            return {"ok": True, "computer": self.computer, "user": "NewUser"}
        if op == "http":
            if not self.running:
                raise RelayError("relay refused 'http': connection refused")
            path = f["path"]
            if path == "/json/version":
                return {"body": json.dumps({"Browser": "Chrome/142"})}
            if path == "/json/list":
                return {"body": json.dumps(self.tabs)}
            if path.startswith("/json/new?"):
                tab = {"id": "TN", "type": "page", "url": DASH, "title": "",
                       "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/TN"}
                self.tabs.insert(0, tab)
                return {"body": json.dumps(tab)}
            if path.startswith("/json/activate/"):
                return {"body": "Target activated"}
            raise RelayError(f"relay refused 'http': path not allowed: {path}")
        if op == "ws_open":
            return {"sock": 1}
        if op == "ws_send":
            msg = json.loads(f["text"])
            self._pending[msg["id"]] = msg["params"].get("expression", msg["method"])
            return {}
        if op == "ws_recv":
            wanted, expr = self._pending.popitem()
            if expr == "Page.captureScreenshot":
                result: dict[str, Any] = {"data": "iVBORw0KGgo="}
            elif "button.tab" in expr:
                self.presses += 1                     # listeners not bound yet: the click does nothing
                self.pressed = self.pressed or self.presses > self.ignore_presses
                result = {"result": {"value": {"pressed": True}}}
            else:
                result = {"result": {"value": {
                    "href": DASH + "?x=secret#frag", "title": "Dashboard", "visibility": "visible",
                    "active_view": "plan-for-today" if self.pressed else "status-home",
                    "view_active": self.pressed, "heading": self.heading,
                    "status": "Loaded." if self.pressed else "", "tasks": 3}}}
            return {"text": json.dumps({"id": wanted, "result": result})}
        if op == "ws_close":
            return {}
        raise AssertionError(op)

    def close(self) -> None:
        pass


class FakeStarter:
    def __init__(self, relay: FakeRelay, ok: bool = True, why: str = "") -> None:
        self.relay, self.ok, self.why = relay, ok, why
        self.started: list[str] = []

    def start(self, kind, url):                                         # noqa: ANN001
        self.started.append(url)
        if self.ok:
            self.relay.running = True
        return self.ok, self.why


class FakeSessions:
    def __init__(self, placement: BrowserPlacement) -> None:
        self.p = placement

    def placement(self, kind):                                          # noqa: ANN001
        return self.p


ON_DESKTOP = BrowserPlacement(known=True, found=True, pid=42, session=1, desktops=[1])


def chrome(relay: FakeRelay, host: str = "DESKTOP-SHDBATI", placement: BrowserPlacement = ON_DESKTOP,
           starter: FakeStarter | None = None, tmp: Path = Path("/tmp")) -> BatiChrome:
    policy = BatiPolicy(launch_wait_seconds=1, view_wait_seconds=1)
    endpoint = DebugEndpoint(port=policy.port)
    return BatiChrome(policy, BROWSERS["chrome"], relay, RelayTargetDirectory(relay),
                      RelayCdpConnector(relay, endpoint, 5), starter or FakeStarter(relay), FakeSessions(placement),
                      lambda: host, tmp, today=date(2026, 11, 21), sleep=lambda s: None)


def test_wrong_wsl_host_is_refused_before_windows_is_asked():
    relay = FakeRelay()
    with pytest.raises(Refused) as stop:
        chrome(relay, host="DESKTOP-2OBSQMC").status()
    assert stop.value.cause == "wrong_machine" and relay.calls == []


def test_wrong_windows_computer_is_refused():
    with pytest.raises(Refused) as stop:
        chrome(FakeRelay(computer="ROSEMARY46")).status()
    assert stop.value.cause == "wrong_machine"


@pytest.mark.parametrize("url", ["https://evil.example/", "http://localhost:9999/", "http://u:p@localhost:8765/",
                                 "file:///C:/Windows/"])
def test_only_the_dashboard_origin_may_be_opened(url):
    relay = FakeRelay()
    with pytest.raises(Refused) as stop:
        chrome(relay).open(url)
    assert stop.value.cause == "url_not_allowed" and relay.calls == []


def test_only_allowlisted_views_may_be_pressed():
    with pytest.raises(Refused) as stop:
        chrome(FakeRelay()).show_view("service-management")
    assert stop.value.cause == "view_not_allowed"


def test_hidden_session_is_refused():
    hidden = BrowserPlacement(known=True, found=True, pid=1, session=0, desktops=[1])
    with pytest.raises(Refused) as stop:
        chrome(FakeRelay(), placement=hidden).status()
    assert stop.value.cause == "hidden_session"


def test_no_one_signed_in_stops():
    nobody = BrowserPlacement(known=True, found=True, pid=1, session=1, desktops=[])
    with pytest.raises(Refused) as stop:
        chrome(FakeRelay(), placement=nobody).open(DASH)
    assert stop.value.cause == "no_desktop"


def test_open_launches_when_no_debug_chrome_and_refuses_when_launch_fails():
    relay = FakeRelay(running=False)
    starter = FakeStarter(relay)
    data = chrome(relay, starter=starter).open(DASH)
    assert data["launched"] is True and starter.started == [DASH]
    relay2 = FakeRelay(running=False)
    with pytest.raises(Refused) as stop:
        chrome(relay2, starter=FakeStarter(relay2, ok=False, why="nobody is signed in to the Windows desktop")).open(DASH)
    assert stop.value.cause == "no_desktop"


def test_open_reuses_a_dashboard_tab():
    relay = FakeRelay()
    data = chrome(relay).open(DASH)
    assert data["reused_tab"] is True
    assert not any(op == "http" and f["path"].startswith("/json/new") for op, f in relay.calls)


def test_plan_for_today_verifies_by_read_back_and_strips_the_query(tmp_path):
    relay = FakeRelay()
    ok, steps = chrome(relay, tmp=tmp_path).plan_for_today(DASH, "abc")
    assert ok, steps["checks"]
    assert steps["read"]["page"]["href"] == DASH
    assert steps["expected_date"] == "November 21, 2026"
    assert (tmp_path / "abc.png").exists()


def test_plan_for_today_with_the_wrong_date_is_not_verified(tmp_path):
    ok, steps = chrome(FakeRelay(heading="Plan for Today — November 22, 2026"), tmp=tmp_path).plan_for_today(DASH, "x")
    assert not ok and steps["checks"]["date_in_heading"] is False


def test_no_tab_closing_and_no_browser_socket():
    directory = RelayTargetDirectory(FakeRelay())
    endpoint = DebugEndpoint()
    with pytest.raises(Exception):
        directory.close_page(endpoint, "T0")
    with pytest.raises(Exception):
        directory.browser_socket(endpoint)


def test_connector_refuses_another_host():
    with pytest.raises(Exception):
        RelayCdpConnector(FakeRelay(), DebugEndpoint(port=9222), 5).connect("ws://10.0.0.5:9222/devtools/page/X")


def test_helpers():
    assert bare("http://localhost:8765/a?token=1#x") == "http://localhost:8765/a"
    assert long_date(date(2026, 11, 21)) == "November 21, 2026"


def test_windows_relay_script_refuses_paths_on_its_own():
    """Run relay_win.py under this Python: the allowlist is enforced on the Windows side too."""
    script = ADWS / "bati_desktop" / "relay_win.py"
    lines = [{"id": 1, "op": "http", "path": "/json/close/A"}, {"id": 2, "op": "ws_open", "path": "/devtools/browser/B"},
             {"id": 3, "op": "http", "method": "DELETE", "path": "/json/list"}, {"id": 4, "op": "nope"},
             {"id": 5, "op": "quit"}]
    done = subprocess.run([sys.executable, "-I", str(script), "1"], input="\n".join(json.dumps(x) for x in lines) + "\n",
                          capture_output=True, text=True, timeout=20)
    answers = [json.loads(x) for x in done.stdout.splitlines()]
    assert [a["ok"] for a in answers] == [False, False, False, False]
    assert all("PermissionError" in a["error"] for a in answers)


def test_a_press_before_the_page_has_bound_its_listeners_is_pressed_again():
    relay = FakeRelay(ignore_presses=1)
    data = chrome(relay).show_view("plan-for-today")
    assert data["presses"] == 2 and relay.pressed and data["status"] == "Loaded."
