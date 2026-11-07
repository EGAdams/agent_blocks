"""Browser Control operations over fake interfaces: routing, errors, browser selection, safety. No browser starts.

    cd adws && ../lancedb_memory/.venv/bin/python -m pytest -q browser_control/tests
"""
from __future__ import annotations

from pathlib import Path

import pytest

from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.interfaces import CdpTarget
from browser_control.catalog import PUBLIC, browser_control_operations
from browser_control.models import BROWSERS
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


class FakeWorld:
    """Starter + endpoint + attacher in one: the machine's browsers."""
    def __init__(self, running=(), launches=True):
        self.running, self.launches, self.started = {n: [CdpTarget(id=f"{n}-1", url="https://a.example/x?token=SECRET", title="A")] for n in running}, launches, []
        self.elements, self.reloads, self.active, self.closed = [FakeElement()], 0, None, []
    # IBrowserStarter
    def start(self, kind, url):
        self.started.append((kind.name, url))
        if self.launches: self.running[kind.name] = [CdpTarget(id=f"{kind.name}-1", url=url, title="Fresh")]
        return self.launches, "" if self.launches else "not installed"
    # IBrowserEndpoint
    def reachable(self, kind): return (kind.name in self.running, "Chrome/1" if kind.name in self.running else "no answer")
    def pages(self, kind): return list(self.running.get(kind.name, []))
    def open_page(self, kind, url):
        t = CdpTarget(id=f"{kind.name}-{len(self.running[kind.name]) + 1}", url=url, title="New"); self.running[kind.name].append(t); return t
    def activate(self, kind, target_id): self.active = target_id
    def close_page(self, kind, target_id): self.closed.append(target_id); self.running[kind.name] = [t for t in self.running[kind.name] if t.id != target_id]
    # IPageAttacher
    def attach(self, kind, tab_id):
        pages = self.running[kind.name]
        tab = next((t for t in pages if t.id == tab_id), pages[0]) if tab_id else pages[0]
        self.page = FakePage(self, tab); return self.page


def make(tmp_path, **world_args):
    world = FakeWorld(**world_args)
    plane = ControlPlane(StaticBrowserCatalog(), world, world, world, FakeClock(), tmp_path, ready_seconds=5)
    return browser_control_operations(plane), world


def run(ops, name, **args): return ops[PUBLIC[name]].run(args)


def test_every_documented_operation_is_registered(tmp_path):
    ops, _ = make(tmp_path)
    assert set(PUBLIC) == {f"browser.{n}" for n in ("open", "navigate", "refresh", "tabs", "select_tab", "click", "read", "screenshot", "status", "close")}
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
    assert not out.ok and "never answered" in out.line


def test_browser_selection_default_chrome_and_edge(tmp_path):
    ops, world = make(tmp_path)
    run(ops, "browser.open", browser="default"); run(ops, "browser.open", browser="Microsoft Edge")
    assert [n for n, _ in world.started] == ["chrome", "edge"] and BROWSERS["edge"].port != BROWSERS["chrome"].port
    assert not run(ops, "browser.open", browser="netscape").ok


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


def test_read_returns_visible_text_only_and_status_lists_both_browsers(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.read")
    assert out.ok and out.data["text"] == "Hello\n\nWorld"
    status = run(ops, "browser.status")
    assert [b["running"] for b in status.data["browsers"]] == [True, False]


def test_screenshot_writes_a_real_file(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.screenshot")
    assert out.ok and Path(out.data["path"]).read_bytes().startswith(b"\x89PNG")
