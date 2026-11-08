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
    """A node. `selectors=None` answers to every query (what the click tests want); a set answers
    only to those, so a form test can have two controls on one page."""

    def __init__(self, text="Notes", tag="a", kind="", selectors=None, attrs=None, options=None,
                 value="", shown=True, enabled=True, size=10, needs_open=None, sets=None):
        self.text, self.tag_name, self._kind, self.clicked = text, tag, kind, False
        self.selectors, self.shown, self.enabled, self.size = selectors, shown, enabled, size
        self.attrs = dict(attrs or {})
        self.option_rows = [dict(o) for o in (options or [])]
        self.held, self.typed, self.chose = value, None, None
        # A site's own dropdown: its items are in the page only while it is open (`needs_open`), and
        # clicking one is what puts its words into the control (`sets`).
        self.needs_open, self.sets = needs_open, sets

    def matches(self, selector):
        if self.needs_open is not None and not self.needs_open.clicked:
            return False
        if self.selectors is None:
            return True
        return selector in self.selectors or any(p.strip() in self.selectors for p in selector.split(","))

    def get_attribute(self, name):
        if name == "type" and "type" not in self.attrs: return self._kind
        return self.attrs.get(name)
    def is_displayed(self): return self.shown
    def is_enabled(self): return self.enabled
    @property
    def rect(self): return {"x": 0, "y": 0, "width": self.size, "height": self.size}
    def click(self):
        self.clicked = True
        if self.sets is not None:
            self.sets[0].held = self.sets[1]

    # -- the form primitives CdpElement provides -----------------------------------
    def value(self): return self.held
    def options(self): return [dict(o) for o in self.option_rows]
    def choose(self, value):
        hit = next((o for o in self.option_rows if o.get("value") == value), None)
        if hit is None or hit.get("disabled"): return None
        for o in self.option_rows: o["selected"] = o is hit
        self.chose = value
        return self.stuck if self.stuck is not None else value
    stuck = None
    def fill(self, text):
        self.typed = text
        self.held = self.shaped(text) if self.shaped is not None else (text if self.caps is None else text[:self.caps])
        return self.held
    caps = None
    shaped = None    # a field with its own format or its own mask: what it ends up holding


class FakePage:
    def __init__(self, world, tab):
        self.world, self.tab, self.released = world, tab, False
        self.current_url, self.title, self.visible = tab.url, tab.title, True
    def navigate(self, url): self.tab.url = self.current_url = url; self.title = self.tab.title = "Arrived"
    def reload(self): self.world.reloads += 1
    def body_text(self): return "Hello\n\n\n\nWorld"
    def capture_png(self, path): Path(path).write_bytes(b"\x89PNG..."); return True
    def is_foreground(self): return self.world.active == self.tab.id
    def find(self, css): return [e for e in self.world.elements if e.matches(css)]
    def form_controls(self, limit=60): return self.world.controls
    def release(self): self.released = True
    def __post_attach__(self):
        """A page Chrome can be told where to save. A world with `can_download=False` has none, which is
        how the refusal for a browser that cannot be told is tested."""
        if self.world.can_download:
            self.configure_downloads = self._configure_downloads
    def _configure_downloads(self, directory):
        self.world.download_dir = directory; self.world.download_dirs.append(directory)


class FakeLink:
    def __init__(self, up=True, mode="mirrored"): self.up, self.mode, self.ensured = up, mode, 0
    def ensure(self):
        self.ensured += 1
        return (True, "") if self.up else (False, "WSL cannot start Windows programs from here")
    def explain_unreachable(self, port): return f"networking is {self.mode}" if self.mode != "mirrored" else "nothing answered"


class FakeWorld:
    """Starter + endpoint + attacher in one: the machine's browsers."""
    def __init__(self, running=(), launches=True):
        self.running, self.launches, self.started = {n: [CdpTarget(id=f"{n}-1", url="https://a.example/x?token=SECRET", title="A")] for n in running}, launches, []
        self.elements, self.reloads, self.active, self.closed = [FakeElement()], 0, None, []
        self.controls = {"total": 0, "controls": []}
        self.shutdowns, self.closes, self.session, self.start_session, self.known = [], True, 1, 1, True
        self.can_download, self.download_dir, self.download_dirs = True, None, []
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
        self.page = FakePage(self, tab); self.page.__post_attach__(); return self.page


def make(tmp_path, link=None, sessions=True, downloads=None, **world_args):
    world = FakeWorld(**world_args)
    link = link or FakeLink()
    parts = tuple(downloads or ()) + (None,) * (4 - len(downloads or ()))
    plane = ControlPlane(StaticBrowserCatalog(), world, world, world, FakeClock(), link, tmp_path, ready_seconds=5,
                         sessions=world if sessions else None,
                         folder=parts[0], filer=parts[1], verifier=parts[2], inspector=parts[3])
    return browser_control_operations(plane), world


def run(ops, name, **args): return ops[PUBLIC[name]].run(args)


def test_every_documented_operation_is_registered(tmp_path):
    ops, _ = make(tmp_path)
    assert set(PUBLIC) == {f"browser.{n}" for n in ("open", "navigate", "refresh", "tabs", "select_tab", "click", "read",
                                                    "screenshot", "status", "close", "dashboard_notes",
                                                    "form_controls", "select_option", "fill", "download")}
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


def test_click_on_ambiguous_words_names_the_candidates_instead_of_guessing(tmp_path):
    """Accela's bug: "More Details" is a heading, the row holding it, and the control that opens it.
    The resolver must refuse all three and the refusal must name the selector that works."""
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [FakeElement(text="More Details", tag="h1"), FakeElement(text="More Details", tag="span")]
    world.controls = {"total": 1, "controls": [
        {"role": "button", "tag": "a", "id": "lnkMoreDetail", "label": "Expand More Details",
         "css": "#lnkMoreDetail", "text": "", "visible": False}]}
    out = run(ops, "browser.click", text="More Details")
    assert not out.ok and not any(e.clicked for e in world.elements)
    assert "more than one thing on the screen" in out.line
    assert "browser.form_controls" in out.line and "#lnkMoreDetail" in out.line
    assert "--css=" in out.line


def test_click_on_a_control_that_is_in_the_page_but_not_on_it_says_to_open_it_first(tmp_path):
    """A collapsed section or a closed menu: Accela keeps the Record Details tab in the DOM with
    display:none, so the only match is hidden and pressing it would do nothing."""
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [FakeElement(text="Record Details", tag="a", shown=False)]
    out = run(ops, "browser.click", text="Record Details")
    assert not out.ok and not world.elements[0].clicked
    assert "not on the screen" in out.line and "collapsed section" in out.line
    assert "browser.form_controls" in out.line


def test_click_that_simply_finds_nothing_gets_no_targeting_lecture(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = []
    out = run(ops, "browser.click", text="Missing")
    assert not out.ok and "form_controls" not in out.line


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


# ---- the form operations ----------------------------------------------------------------------------

def dropdown(selectors=("#ddl", "select"), **more):
    """A Record Type list like Accela's, PLAT Records among its options."""
    rows = [{"position": 1, "value": "", "text": "--Select--", "selected": True, "disabled": False},
            {"position": 2, "value": "OLSR/PLAT/NA/NA", "text": "PLAT Records", "selected": False, "disabled": False},
            {"position": 3, "value": "OLSR/CORNER/NA/NA", "text": "CORNER Records", "selected": False, "disabled": False},
            {"position": 4, "value": "OLSR/OLD/NA/NA", "text": "Retired Records", "selected": False, "disabled": True}]
    return FakeElement(text="", tag="select", selectors=set(selectors), options=rows,
                       attrs={"aria-label": "Record Type:", "id": "ddl"}, **more)


def field(selectors=("#txt", "input"), kind="text", attrs=None, **more):
    named = {"aria-label": "Project Name:", "id": "txt", **(attrs or {})}
    return FakeElement(text="", tag="input", kind=kind, selectors=set(selectors), attrs=named, **more)


def test_select_option_chooses_by_visible_text_and_proves_it(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown()]
    out = run(ops, "browser.select_option", css="#ddl", option_text="PLAT Records")
    assert out.ok and out.data["verified"] and out.data["selected_value"] == "OLSR/PLAT/NA/NA"
    assert world.elements[0].chose == "OLSR/PLAT/NA/NA" and "confirms it" in out.line


def test_select_option_chooses_by_value(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown()]
    out = run(ops, "browser.select_option", css="#ddl", value="OLSR/CORNER/NA/NA")
    assert out.ok and out.data["selected"] == "CORNER Records" and out.data["position"] == 3


def test_select_option_forgives_a_form_label_the_plain_click_locator_would_miss(tmp_path):
    """The page says "Record Type:"; a person says "Record Type". That mismatch is why a label
    that is plainly on the screen used to find nothing."""
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown()]
    out = run(ops, "browser.select_option", label="Record Type", option_text="PLAT Records")
    assert out.ok and out.data["selected"] == "PLAT Records"


def test_select_option_lists_the_options_when_none_match(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown()]
    out = run(ops, "browser.select_option", css="#ddl", option_text="Plat Maps")
    assert not out.ok and out.data["cause"] == "no_such_option" and not out.data["verified"]
    assert "PLAT Records" in out.line and len(out.data["options"]) == 4 and world.elements[0].chose is None


def test_select_option_refuses_an_ambiguous_request_instead_of_guessing(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown()]
    out = run(ops, "browser.select_option", css="#ddl", option_text="Records")
    assert not out.ok and "matches 3 options" in out.line and world.elements[0].chose is None


def test_select_option_refuses_a_disabled_option(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown()]
    out = run(ops, "browser.select_option", css="#ddl", option_text="Retired Records")
    assert not out.ok and "disabled" in out.line and world.elements[0].chose is None


def test_select_option_refuses_a_disabled_dropdown(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown(enabled=False)]
    out = run(ops, "browser.select_option", css="#ddl", option_text="PLAT Records")
    assert not out.ok and out.data["cause"] == "disabled" and "disabled" in out.line


def test_select_option_refuses_two_matching_controls(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown(), dropdown()]
    out = run(ops, "browser.select_option", css="#ddl", option_text="PLAT Records")
    assert not out.ok and out.data["cause"] == "ambiguous_control" and "browser.form_controls" in out.line


def test_select_option_says_so_when_a_control_is_not_there(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [dropdown()]
    out = run(ops, "browser.select_option", css="#nope", option_text="PLAT Records")
    assert not out.ok and out.data["cause"] == "control_not_found" and out.data["attempts"]


def test_select_option_refuses_something_that_is_not_a_dropdown(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field()]
    out = run(ops, "browser.select_option", css="#txt", option_text="PLAT Records")
    assert not out.ok and out.data["cause"] == "not_a_dropdown"


def test_select_option_reports_a_list_that_did_not_keep_the_choice(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    list_ = dropdown()
    list_.stuck = ""                      # the page puts it straight back
    world.elements = [list_]
    out = run(ops, "browser.select_option", css="#ddl", option_text="PLAT Records")
    assert not out.ok and out.data["cause"] == "not_accepted" and not out.data["verified"]


def test_select_option_is_honest_when_choosing_reloaded_the_form(tmp_path):
    """A dropdown that posts the form back replaces the document, so the second read is gone.
    The choice is still verified -- the list reported its own value as it changed."""
    ops, world = make(tmp_path, running=["chrome"])
    list_ = dropdown()
    world.elements = [list_]

    def gone(value, _original=list_.choose):
        landed = _original(value)
        world.elements = []
        return landed
    list_.choose = gone
    out = run(ops, "browser.select_option", css="#ddl", option_text="PLAT Records")
    assert out.ok and out.data["verified"] and "no longer there" in out.line


def test_fill_types_into_a_field_and_reads_it_back(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field()]
    out = run(ops, "browser.fill", css="#txt", text="Fralick's Addition")
    assert out.ok and out.data["verified"] and out.data["value"] == "Fralick's Addition"
    assert world.elements[0].typed == "Fralick's Addition"


def test_fill_replaces_rather_than_appends(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field(value="old text")]
    out = run(ops, "browser.fill", css="#txt", text="new")
    assert out.ok and world.elements[0].held == "new"


def test_fill_works_a_search_input(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field(kind="search")]
    assert run(ops, "browser.fill", css="#txt", text="plats").ok


def test_fill_never_touches_a_password_field(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [FakeElement(text="", tag="input", kind="password", selectors={"#pw", "input"},
                                 attrs={"id": "pw"})]
    out = run(ops, "browser.fill", css="#pw", text="hunter2")
    assert not out.ok and out.data["cause"] == "credential_field" and world.elements[0].typed is None
    assert "hunter2" not in out.line and "password" in out.line


def test_fill_refuses_a_field_whose_name_says_credential(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [FakeElement(text="", tag="input", kind="text", selectors={"#t", "input"},
                                 attrs={"id": "t", "name": "ctl00$one_time_code"})]
    out = run(ops, "browser.fill", css="#t", text="123456")
    assert not out.ok and out.data["cause"] == "credential_field" and world.elements[0].typed is None


def test_fill_refuses_a_hidden_token_field(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [FakeElement(text="", tag="input", kind="hidden", selectors={"#vs", "input"},
                                 attrs={"id": "vs"}, shown=False)]
    out = run(ops, "browser.fill", css="#vs", text="x")
    assert not out.ok and out.data["cause"] == "not_visible" and world.elements[0].typed is None


def test_fill_refuses_a_read_only_field(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field(attrs={"id": "txt", "readonly": "readonly"})]
    out = run(ops, "browser.fill", css="#txt", text="x")
    assert not out.ok and out.data["cause"] == "readonly" and world.elements[0].typed is None


def test_fill_sends_a_checkbox_to_click_and_a_dropdown_to_select_option(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field(kind="checkbox")]
    out = run(ops, "browser.fill", css="#txt", text="x")
    assert not out.ok and out.data["cause"] == "not_editable" and "browser.click" in out.line
    world.elements = [dropdown()]
    out = run(ops, "browser.fill", css="#ddl", text="x")
    assert not out.ok and "browser.select_option" in out.line


def test_fill_never_puts_a_file_into_a_page(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field(kind="file")]
    out = run(ops, "browser.fill", css="#txt", text="/etc/passwd")
    assert not out.ok and out.data["cause"] == "not_editable"


def test_fill_reports_a_field_that_changed_what_was_typed(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    short = field()
    short.caps = 4
    world.elements = [short]
    out = run(ops, "browser.fill", css="#txt", text="Fralick's Addition")
    assert not out.ok and out.data["cause"] == "value_differs" and out.data["value"] == "Fral"
    assert not out.data["verified"] and "Nothing was submitted" in out.line


def test_a_form_operation_needs_a_control_named(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field()]
    out = run(ops, "browser.fill", text="x")
    assert not out.ok and "--css" in out.line and "--label" in out.line
    out = run(ops, "browser.fill", css="#txt")
    assert not out.ok and "--text" in out.line


def test_form_operations_target_a_tab_by_its_stable_id(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.running["chrome"].append(CdpTarget(id="chrome-2", url="https://b.example/f", title="Form"))
    world.elements = [dropdown()]
    out = run(ops, "browser.select_option", tab="chrome-2", css="#ddl", option_text="PLAT Records")
    assert out.ok and world.page.tab.id == "chrome-2"
    assert not run(ops, "browser.fill", tab="chrome-9", css="#txt", text="x").ok


def test_form_controls_lists_fields_dropdowns_and_buttons(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.controls = {"total": 3, "controls": [
        {"role": "dropdown", "tag": "select", "id": "ddl", "label": "Record Type:", "css": "#ddl",
         "options": [{"position": 1, "value": "", "text": "--Select--", "selected": True},
                     {"position": 2, "value": "OLSR/PLAT/NA/NA", "text": "PLAT Records"}],
         "selected": "--Select--", "selected_value": ""},
        {"role": "text", "tag": "input", "type": "text", "id": "txt", "label": "Project Name:", "css": "#txt"},
        {"role": "button", "tag": "a", "label": "Search", "css": "#btnSearch", "text": "Search"}]}
    out = run(ops, "browser.form_controls")
    assert out.ok and out.data["verified"] and out.data["total"] == 3
    assert "'Record Type:' css=#ddl" in out.data["listing"] and "'PLAT Records'=value 'OLSR/PLAT/NA/NA'" in out.data["listing"]
    assert "[button] 'Search'" in out.data["listing"]
    assert out.data["controls"][0]["options"][1]["text"] == "PLAT Records"


def test_form_controls_reports_a_page_with_nothing_to_operate(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.form_controls")
    assert out.ok and out.data["total"] == 0 and "no fields" in out.line


def test_form_controls_marks_a_credential_field_as_never_filled(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.controls = {"total": 1, "controls": [{"role": "password", "tag": "input", "type": "password",
                                                "id": "pw", "label": "Password", "css": "#pw", "secret": True}]}
    out = run(ops, "browser.form_controls")
    assert out.ok and "NEVER filled or read" in out.data["listing"] and out.data["controls"][0]["secret"]


def test_form_controls_limit_is_a_number_within_bounds(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    assert run(ops, "browser.form_controls", limit="40").ok
    assert not run(ops, "browser.form_controls", limit="lots").ok


def test_a_click_on_a_dropdown_item_now_says_which_operation_to_use(tmp_path):
    """The original failure: PLAT Records is an <option>, which Chrome gives no box, so no
    resolver can ever reach it. Instead of "I could not find one clickable element", point at
    the operation that can."""
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = []
    world.controls = {"total": 1, "controls": [
        {"role": "dropdown", "tag": "select", "css": "#ddl", "label": "Record Type:",
         "options": [{"position": 2, "value": "OLSR/PLAT/NA/NA", "text": "PLAT Records"}]}]}
    out = run(ops, "browser.click", text="PLAT Records")
    assert not out.ok and "browser.select_option" in out.line and "#ddl" in out.line


# ---- a dropdown the site draws itself ---------------------------------------------------------------
# Opposite act, same operation: a native <option> has no box and is chosen by value, while a site's own
# item is a node the page lays out, so it has a box, is clicked, and is not in the page until it opens.

def site_dropdown(attrs=None, items=("PLAT Records", "CORNER Records"), owns=False):
    """A role=combobox trigger and the items it shows once opened. Returns (trigger, [items])."""
    named = {"role": "combobox", "aria-expanded": "false", "id": "combo", **(attrs or {})}
    if owns:
        named["aria-controls"] = "combo-list"
    trigger = FakeElement(text="--Select--", tag="div", selectors={"#combo", "[role=combobox]"}, attrs=named)
    scope = {"[role=option]", '[id="combo-list"] [role=option]'} if owns else {"[role=option]"}
    shown = [FakeElement(text=t, tag="div", selectors=scope, attrs={"role": "option", "data-value": t.split()[0]},
                         needs_open=trigger, sets=(trigger, t)) for t in items]
    return trigger, shown


def test_select_option_works_a_dropdown_the_site_draws_itself(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    trigger, items = site_dropdown()
    world.elements = [trigger, *items]
    out = run(ops, "browser.select_option", css="#combo", option_text="PLAT Records")
    assert out.ok and out.data["verified"] and out.data["site_drawn"] is True
    assert trigger.clicked and items[0].clicked and not items[1].clicked
    assert out.data["selected"] == "PLAT Records" and "PLAT Records" in out.line


def test_a_site_drawn_list_is_opened_before_its_items_are_read(tmp_path):
    """The items are not in the page until the control is open, so reading them first finds nothing
    and clicking blind would hit empty space."""
    ops, world = make(tmp_path, running=["chrome"])
    trigger, items = site_dropdown()
    world.elements = [trigger, *items]
    assert not items[0].matches("[role=option]")        # nothing to find before it opens
    assert run(ops, "browser.select_option", css="#combo", option_text="CORNER Records").ok
    assert items[1].matches("[role=option]")


def test_a_site_drawn_list_prefers_the_list_it_says_is_its_own(tmp_path):
    """Two of these on one form: aria-controls keeps the choice inside the right one."""
    ops, world = make(tmp_path, running=["chrome"])
    trigger, items = site_dropdown(owns=True)
    other = FakeElement(text="PLAT Records", tag="div", selectors={"[role=option]"},
                        attrs={"role": "option"})      # another control's item, already showing
    world.elements = [trigger, other, *items]
    out = run(ops, "browser.select_option", css="#combo", option_text="PLAT Records")
    assert out.ok and items[0].clicked and not other.clicked


def test_a_site_drawn_list_that_shows_no_items_says_so(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    trigger, _ = site_dropdown(items=())
    world.elements = [trigger]
    out = run(ops, "browser.select_option", css="#combo", option_text="PLAT Records")
    assert not out.ok and out.data["cause"] == "no_options_shown" and "browser.fill" in out.line


def test_a_site_drawn_list_lists_what_it_does_offer(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    trigger, items = site_dropdown()
    world.elements = [trigger, *items]
    out = run(ops, "browser.select_option", css="#combo", option_text="Deeds")
    assert not out.ok and out.data["cause"] == "no_such_option"
    assert "'PLAT Records'" in out.line and not items[0].clicked


def test_a_site_drawn_list_that_shows_something_else_afterwards_is_not_a_success(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    trigger, items = site_dropdown()
    items[0].sets = (trigger, "CORNER Records")        # the widget put a different item in
    world.elements = [trigger, *items]
    out = run(ops, "browser.select_option", css="#combo", option_text="PLAT Records")
    assert not out.ok and out.data["cause"] == "did_not_stick" and "CORNER Records" in out.line


def test_a_site_drawn_list_with_nothing_to_read_back_is_reported_unverified(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    trigger, items = site_dropdown()
    trigger.text = ""
    items[0].sets = None                               # the control shows no text of its own
    world.elements = [trigger, *items]
    out = run(ops, "browser.select_option", css="#combo", option_text="PLAT Records")
    assert items[0].clicked and out.data["verified"] is False and "cannot prove" in out.line


def test_a_site_drawn_choice_reads_back_from_the_item_it_points_at(tmp_path):
    """Some widgets leave the trigger blank and name the chosen item with aria-activedescendant."""
    ops, world = make(tmp_path, running=["chrome"])
    trigger, items = site_dropdown()
    trigger.text = ""
    items[0].sets = None
    items[0].attrs["id"] = "opt-plat"
    items[0].selectors.add('[id="opt-plat"]')
    trigger.attrs["aria-activedescendant"] = "opt-plat"
    world.elements = [trigger, *items]
    out = run(ops, "browser.select_option", css="#combo", option_text="PLAT Records")
    assert out.ok and out.data["verified"] and "PLAT Records" in out.line


def test_something_with_no_list_about_it_is_still_not_a_dropdown(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [FakeElement(text="hello", tag="div", selectors={"#d"}, attrs={"id": "d"})]
    out = run(ops, "browser.select_option", css="#d", option_text="PLAT Records")
    assert not out.ok and out.data["cause"] == "not_a_dropdown" and "browser.form_controls" in out.line


def test_form_controls_says_which_dropdowns_the_site_draws_itself(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.controls = {"total": 1, "controls": [{"role": "dropdown", "tag": "div", "css": "#combo",
                                                "label": "Record Type:", "site_drawn": True,
                                                "selected": "--Select--"}]}
    out = run(ops, "browser.form_controls")
    assert out.ok and "the site draws this list itself" in out.line and "now '--Select--'" in out.line


# ---- a field with its own format or its own mask ----------------------------------------------------

def test_fill_accepts_a_field_that_puts_its_own_punctuation_in(tmp_path):
    """A phone field that answers "(616) 555-1212" took "6165551212" -- same text, its own shape."""
    ops, world = make(tmp_path, running=["chrome"])
    masked = field()
    masked.shaped = lambda typed: "(616) 555-1212"
    world.elements = [masked]
    out = run(ops, "browser.fill", css="#txt", text="6165551212")
    assert out.ok and out.data["verified"] and out.data["value"] == "(616) 555-1212"
    assert "reformatted" in out.line


def test_fill_tells_a_date_field_what_format_it_wants(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    picker = field(kind="date")
    picker.shaped = lambda typed: ""                   # a date input ignores anything but YYYY-MM-DD
    world.elements = [picker]
    out = run(ops, "browser.fill", css="#txt", text="31/12/2025")
    assert not out.ok and out.data["cause"] == "wrong_format" and "YYYY-MM-DD" in out.line


def test_fill_fills_a_date_field_given_the_format_it_wants(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    world.elements = [field(kind="date")]
    out = run(ops, "browser.fill", css="#txt", text="2025-12-31")
    assert out.ok and out.data["verified"] and out.data["value"] == "2025-12-31"


def test_fill_calls_a_length_limit_what_it_is(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    short = field(attrs={"maxlength": "4"})
    short.caps = 4
    world.elements = [short]
    out = run(ops, "browser.fill", css="#txt", text="Fralick's Addition")
    assert not out.ok and out.data["cause"] == "truncated" and "at most 4 characters" in out.line


def test_fill_says_when_a_field_took_no_text_at_all(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    stubborn = field()
    stubborn.shaped = lambda typed: ""
    world.elements = [stubborn]
    out = run(ops, "browser.fill", css="#txt", text="Fralick's Addition")
    assert not out.ok and out.data["cause"] == "refused_text" and "still empty" in out.line
