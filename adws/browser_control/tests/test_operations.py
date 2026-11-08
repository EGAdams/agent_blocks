"""Browser Control operations over fake interfaces: routing, errors, browser selection, safety. No browser starts.

    cd adws && ../lancedb_memory/.venv/bin/python -m pytest -q browser_control/tests
"""
from __future__ import annotations

from pathlib import Path

import pytest

from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.interfaces import CdpTarget
from browser_control.catalog import PUBLIC, browser_control_operations
from browser_control.models import BROWSERS, BrowserPlacement
from browser_control.operations import safe_url
from browser_control.plane import ControlPlane, StaticBrowserCatalog, checked_url


class FakeClock:
    def __init__(self): self.t = 0.0
    def monotonic(self): return self.t
    def sleep(self, seconds): self.t += seconds


class FakeElement:
    def __init__(self, text="Notes", tag="a", kind=""): self.text, self.tag_name, self._kind, self.clicked = text, tag, kind, False
    def get_attribute(self, name): return self._kind if name == "type" else None
    def is_displayed(self): return True
    def is_enabled(self): return True
    @property
    def rect(self): return {"x": 0, "y": 0, "width": 10, "height": 10}
    def click(self): self.clicked = True


class FakePage:
    def __init__(self, world, tab):
        self.world, self.tab, self.released = world, tab, False
        self.current_url, self.title, self.visible = tab.url, tab.title, True
    def navigate(self, url): self.tab.url = self.current_url = url; self.title = self.tab.title = "Arrived"
    def reload(self): self.world.reloads += 1
    def body_text(self): return "Hello\n\n\n\nWorld"
    def capture_png(self, path): Path(path).write_bytes(b"\x89PNG..."); return True
    def is_foreground(self): return self.world.active == self.tab.id
    def find(self, css): return list(self.world.elements)
    def release(self): self.released = True


class FakeLink:
    def __init__(self, up=True, mode="mirrored"): self.up, self.mode = up, mode
    def ensure(self): return (True, "") if self.up else (False, "WSL cannot start Windows programs from here")
    def explain_unreachable(self, port): return f"networking is {self.mode}" if self.mode != "mirrored" else "nothing answered"


class FakeWorld:
    """Starter + endpoint + attacher in one: the machine's browsers."""
    def __init__(self, running=(), launches=True):
        self.running, self.launches, self.started = {n: [CdpTarget(id=f"{n}-1", url="https://a.example/x?token=SECRET", title="A")] for n in running}, launches, []
        self.elements, self.reloads, self.active, self.closed = [FakeElement()], 0, None, []
        self.shutdowns, self.closes, self.session, self.start_session, self.known = [], True, 1, 1, True
    # IBrowserStarter
    def start(self, kind, url):
        self.started.append((kind.name, url)); self.session = self.start_session
        if self.launches: self.running[kind.name] = [CdpTarget(id=f"{kind.name}-1", url=url, title="Fresh")]
        return self.launches, "" if self.launches else "not installed"
    # IBrowserEndpoint
    def reachable(self, kind): return (kind.name in self.running, "Chrome/1" if kind.name in self.running else "no answer")
    def pages(self, kind): return list(self.running.get(kind.name, []))
    def open_page(self, kind, url):
        t = CdpTarget(id=f"{kind.name}-{len(self.running[kind.name]) + 1}", url=url, title="New"); self.running[kind.name].append(t); return t
    def activate(self, kind, target_id): self.active = target_id
    def close_page(self, kind, target_id): self.closed.append(target_id); self.running[kind.name] = [t for t in self.running[kind.name] if t.id != target_id]
    def shutdown(self, kind):
        self.shutdowns.append(kind.name)
        if self.closes: self.running.pop(kind.name, None)
    # IWindowSessions: a fresh start lands in `start_session`; what was already running sits in `session`
    def placement(self, kind):
        if self.known is False: return BrowserPlacement(known=False, reason="powershell timed out")
        if kind.name not in self.running: return BrowserPlacement(known=True, found=False, desktops=[1])
        return BrowserPlacement(known=True, found=True, pid=42, session=self.session, desktops=[1])
    # IPageAttacher
    def attach(self, kind, tab_id):
        pages = self.running[kind.name]
        tab = next((t for t in pages if t.id == tab_id), pages[0]) if tab_id else pages[0]
        self.page = FakePage(self, tab); return self.page


def make(tmp_path, link=None, sessions=True, **world_args):
    world = FakeWorld(**world_args)
    link = link or FakeLink()
    plane = ControlPlane(StaticBrowserCatalog(), world, world, world, FakeClock(), link, tmp_path, ready_seconds=5,
                         sessions=world if sessions else None)
    return browser_control_operations(plane), world


def run(ops, name, **args): return ops[PUBLIC[name]].run(args)


def test_every_documented_operation_is_registered(tmp_path):
    ops, _ = make(tmp_path)
    assert set(PUBLIC) == {f"browser.{n}" for n in ("open", "navigate", "refresh", "tabs", "select_tab", "click", "read", "screenshot", "status", "close", "dashboard_notes")}
    assert all(op.description.startswith(name) for name, op in ((n, ops[v]) for n, v in PUBLIC.items()))


def test_open_starts_the_browser_and_verifies_it(tmp_path):
    ops, world = make(tmp_path)
    out = run(ops, "browser.open", url="https://example.com")
    assert out.ok and out.data["started"] and out.data["verified"] and world.started == [("chrome", "https://example.com")]


def test_open_never_starts_a_second_browser_and_adds_a_tab(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.open", url="https://b.example")
    assert out.ok and not out.data["started"] and not world.started and len(world.running["chrome"]) == 2


def test_open_reports_a_browser_that_will_not_start(tmp_path):
    ops, _ = make(tmp_path, launches=False)
    out = run(ops, "browser.open")
    assert not out.ok and "not installed" in out.line and out.data["verified"] is False


def test_open_reports_a_port_that_never_answers(tmp_path):
    ops, world = make(tmp_path)
    world.start = lambda kind, url: (True, "")             # claims to launch, never comes up
    out = run(ops, "browser.open")
    assert not out.ok and "could not control it" in out.line


def test_chrome_is_the_only_browser_and_default_means_chrome(tmp_path):
    ops, world = make(tmp_path)
    assert run(ops, "browser.open", browser="default").ok and [n for n, _ in world.started] == ["chrome"]
    assert list(BROWSERS) == ["chrome"]
    for other in ("edge", "Microsoft Edge", "msedge", "firefox", "netscape"):
        out = run(ops, "browser.open", browser=other)
        assert not out.ok and "only authorized browser" in out.line
    assert [n for n, _ in world.started] == ["chrome"], "no other browser may ever be launched"
    assert not run(ops, "browser.status", browser="edge").ok


def test_open_says_when_windows_programs_cannot_be_started(tmp_path):
    ops, world = make(tmp_path, link=FakeLink(up=False))
    out = run(ops, "browser.open")
    assert not out.ok and out.data["cause"] == "windows_interop" and not world.started


def test_open_names_the_networking_cause_when_the_port_is_unreachable(tmp_path):
    ops, world = make(tmp_path, link=FakeLink(mode="nat"))
    world.start = lambda kind, url: (True, "")
    out = run(ops, "browser.open")
    assert not out.ok and out.data["cause"] == "control_port" and "networking is nat" in out.line


def test_navigate_refuses_anything_but_http(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "chrome://settings", "ftp://x.example", ""):
        assert not run(ops, "browser.navigate", url=bad).ok
    assert checked_url("example.com/path") == "https://example.com/path"


def test_navigate_then_refresh_report_where_the_tab_is(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.navigate", url="https://dash.example/home")
    assert out.ok and out.data["url"] == "https://dash.example/home" and world.page.released
    assert run(ops, "browser.refresh").ok and world.reloads == 1


def test_a_browser_that_is_not_running_gives_a_useful_error(tmp_path):
    ops, _ = make(tmp_path)
    out = run(ops, "browser.refresh")
    assert not out.ok and "not running" in out.line and "open a browser" in out.line


def test_urls_never_carry_their_query_into_results(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.tabs")
    assert "SECRET" not in out.line and "SECRET" not in str(out.data) and safe_url("https://a/b?x=1#f") == "https://a/b?…"


def test_select_tab_by_number_activates_and_checks_the_page(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    run(ops, "browser.open", url="https://b.example")
    out = run(ops, "browser.select_tab", tab="2")
    assert out.ok and world.active == "chrome-2"
    assert not run(ops, "browser.select_tab", tab="9").ok and not run(ops, "browser.select_tab").ok


def test_close_needs_an_explicit_tab_and_keeps_the_last_one(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    assert not run(ops, "browser.close").ok
    assert "only open tab" in run(ops, "browser.close", tab="1").line and not world.closed
    run(ops, "browser.open", url="https://b.example")
    assert run(ops, "browser.close", tab="2").ok and world.closed == ["chrome-2"] and len(world.running["chrome"]) == 1


def test_click_presses_one_element_and_names_it(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.click", text="Notes")
    assert out.ok and world.elements[0].clicked and out.data["clicked"] == "Notes"


def test_click_refuses_consequential_words_until_confirmed(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [FakeElement("Pay now")]
    out = run(ops, "browser.click", text="Pay now")
    assert not out.ok and out.data["needs_confirmation"] and not world.elements[0].clicked
    assert run(ops, "browser.click", text="Pay now", confirm=True).ok and world.elements[0].clicked


def test_click_never_touches_a_password_field_and_needs_a_target(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [FakeElement("", "input", "password")]
    out = run(ops, "browser.click", css="input")
    assert not out.ok and "password" in out.line and not world.elements[0].clicked
    with pytest.raises(BrowserWorkflowError):
        from browser_workflows.operations.common import locators_from; locators_from({})
    assert not run(ops, "browser.click").ok


def test_click_that_finds_nothing_says_so(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = []
    out = run(ops, "browser.click", text="Missing")
    assert not out.ok and "could not find" in out.line


def test_read_returns_visible_text_only_and_status_lists_only_chrome(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.read")
    assert out.ok and out.data["text"] == "Hello\n\nWorld"
    status = run(ops, "browser.status")
    assert [b["browser"] for b in status.data["browsers"]] == ["chrome"] and status.data["browsers"][0]["running"]


def test_screenshot_writes_a_real_file(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.screenshot")
    assert out.ok and Path(out.data["path"]).read_bytes().startswith(b"\x89PNG")


# -- launch and visibility phases ----------------------------------------------

def test_open_runs_as_three_traced_phases(tmp_path):
    ops, _ = make(tmp_path)
    assert [s.name for s in ops["browser_open"].steps] == ["browser_launch", "browser_visibility", "browser_open"]
    seen = []
    out = ops["browser_open"].run({"url": "https://example.com"}, step_runner=lambda st, a: seen.append(st.name) or st.run(a))
    assert out.ok and seen == ["browser_launch", "browser_visibility", "browser_open"]
    assert "on your desktop in Windows session 1" in out.line and out.data["visible"] is True


def test_a_hidden_chrome_is_closed_and_a_visible_one_started(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.session = 0                                     # the old interop launch: alive, answering, unseen
    out = run(ops, "browser.open", url="https://example.com")
    assert out.ok and world.shutdowns == ["chrome"] and world.started == [("chrome", "https://example.com")]
    assert out.data["replaced_hidden"] and out.data["session"] == 1 and "hidden copy was closed" in out.line


def test_a_hidden_chrome_that_will_not_close_is_reported(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.session, world.closes = 0, False
    out = run(ops, "browser.open")
    assert not out.ok and out.data["cause"] == "hidden_session" and not world.started


def test_a_launch_that_lands_hidden_is_not_called_success(tmp_path):
    ops, world = make(tmp_path)
    world.start_session = 0
    out = run(ops, "browser.open")
    assert not out.ok and out.data["cause"] == "hidden_session" and out.data["verified"] is False
    assert "not on your screen" in out.line and "session (0)" in out.line


def test_open_will_not_claim_visibility_it_could_not_check(tmp_path):
    ops, world = make(tmp_path)
    world.known = False
    out = run(ops, "browser.open")
    assert not out.ok and out.data["cause"] == "visibility_unknown" and "powershell timed out" in out.line
    ops, _ = make(tmp_path, sessions=False)
    assert run(ops, "browser.open").data["cause"] == "visibility_unknown"


def test_a_visible_running_chrome_is_left_alone(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.open", url="https://b.example")
    assert out.ok and not world.shutdowns and not world.started and len(world.running["chrome"]) == 2


# ---- browser.dashboard_notes --------------------------------------------------------------------------------

from browser_control.operations import DASHBOARD_URL, NOTES_TAB_CSS


class NotesButton(FakeElement):
    """The dashboard's NOTES button: its class gains `active` only when it is clicked (unless `sticks` is False)."""
    def __init__(self, sticks=True): super().__init__(text="NOTES", tag="button"); self.sticks = sticks
    def get_attribute(self, name):
        if name == "class": return "tab active" if self.clicked and self.sticks else "tab"
        return super().get_attribute(name)


def notes_world(tmp_path, **kw):
    ops, world = make(tmp_path, **kw)
    world.elements = [NotesButton()]
    return ops, world


def test_dashboard_notes_starts_chrome_on_the_dashboard_and_confirms_notes_is_active(tmp_path):
    ops, world = notes_world(tmp_path)
    out = run(ops, "browser.dashboard_notes")
    assert out.ok and out.data["verified"] and out.data["notes_active"]
    assert world.started == [("chrome", DASHBOARD_URL)] and world.elements[0].clicked
    assert "NOTES tab showing" in out.line and "Windows session 1" in out.line
    seq = ops["browser_dashboard_notes"]
    assert [s.name for s in seq.steps] == ["browser_notes_request", "browser_launch", "browser_visibility",
                                          "browser_dashboard_tab", "browser_click", "browser_notes_verify"]


def test_dashboard_notes_reuses_an_open_dashboard_tab(tmp_path):
    ops, world = notes_world(tmp_path, running=["chrome"])
    world.running["chrome"].append(CdpTarget(id="chrome-dash", url=DASHBOARD_URL + "#notes", title="ROL"))
    out = run(ops, "browser.dashboard_notes")
    assert out.ok and not world.started and len(world.running["chrome"]) == 2
    assert world.active == "chrome-dash" and out.data["tab"] == "chrome-dash" and out.data["opened_tab"] is False


def test_dashboard_notes_opens_a_dashboard_tab_when_none_is_open(tmp_path):
    ops, world = notes_world(tmp_path, running=["chrome"])
    out = run(ops, "browser.dashboard_notes")
    assert out.ok and out.data["opened_tab"] is True and world.running["chrome"][-1].url == DASHBOARD_URL


def test_dashboard_notes_ignores_a_caller_supplied_destination(tmp_path):
    ops, world = notes_world(tmp_path)
    out = run(ops, "browser.dashboard_notes", url="https://evil.example/", css="#pay", text="Pay now")
    assert out.ok and world.started == [("chrome", DASHBOARD_URL)] and out.data["css"] == NOTES_TAB_CSS


def test_dashboard_notes_is_a_failure_when_notes_never_becomes_active(tmp_path):
    ops, world = notes_world(tmp_path)
    world.elements = [NotesButton(sticks=False)]
    out = run(ops, "browser.dashboard_notes")
    assert not out.ok and out.data["cause"] == "notes_not_active" and out.data["verified"] is False


def test_dashboard_notes_is_a_failure_when_the_dashboard_never_loads(tmp_path):
    ops, world = notes_world(tmp_path)
    world.elements = []
    out = run(ops, "browser.dashboard_notes")
    assert not out.ok and out.data["cause"] == "dashboard_not_loaded"


def test_dashboard_notes_stops_on_a_hidden_chrome_it_cannot_replace(tmp_path):
    ops, world = notes_world(tmp_path, running=["chrome"])
    world.session, world.closes = 0, False
    out = run(ops, "browser.dashboard_notes")
    assert not out.ok and out.data["cause"] == "hidden_session" and not world.elements[0].clicked
