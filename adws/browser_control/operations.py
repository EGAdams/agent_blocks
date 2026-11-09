"""The fourteen Browser Control operations. Each is one `Operation` (browser_workflows' base class, so
the ADW harness runs it as a lifecycle phase and the object trace records it) over a ControlPlane.

    browser.open  browser.navigate  browser.refresh  browser.tabs  browser.select_tab
    browser.click browser.read      browser.screenshot browser.status browser.close
    browser.form_controls  browser.select_option  browser.fill

(browser.download is the fourteenth and lives in `downloads.py`, because saving a file to EG's disk
is a power the other thirteen must not have; `click` refuses a file link and names it.)

Every result is verified, not assumed: `open` waits until the DevTools port answers and lists a tab,
`navigate` reports the URL the tab ended on, `close` re-lists the tabs. Nothing here reads cookies,
storage or password fields; URLs are reported without their query or fragment (they carry tokens).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.models import RecordedAction
from browser_workflows.normalizing.consequences import KeywordConsequenceClassifier
from browser_workflows.operations.common import Operation, OperationOutcome, locators_from, ok, refused
from browser_workflows.playback.locating import RankedElementResolver, default_strategy_registry

from .filelinks import saved_extension
from .highlights import blinking, classify_page
from .forms import (OptionMatcher, SecretFieldGuard, TypedValueCheck, WidgetDropdown, form_locators_from,
                    form_strategy_registry, looks_site_drawn, same_characters)
from .models import BrowserKind, FormControlView, OptionView, TabView
from .plane import ControlPlane, checked_url

MAX_READ_CHARS = 20000
MAX_CONTROLS = 200
#: What a person can type into. Anything else is a click (checkbox, radio, submit) or refused (file).
TYPEABLE = ("", "text", "search", "email", "tel", "url", "number", "date", "month", "week", "time",
            "datetime-local", "color", "range")


def safe_url(url: str) -> str:
    """scheme://host/path -- the query and fragment can hold tokens, so they are never reported."""
    parts = urlparse(url or "")
    if not parts.scheme or not parts.netloc:
        return (url or "")[:80]
    return f"{parts.scheme}://{parts.netloc}{parts.path}" + ("?…" if parts.query or parts.fragment else "")


class BrowserOperation(Operation):
    """Shared plumbing: pick the browser, require it to be up, find the chosen tab."""
    performer = "browser_control"
    destructive = False

    def __init__(self, plane: ControlPlane) -> None:
        self._plane = plane

    def _kind(self, args: dict[str, Any]) -> BrowserKind:
        return self._plane.catalog.kind(args.get("browser"))

    def _up(self, kind: BrowserKind) -> str:
        up, detail = self._plane.endpoint.reachable(kind)
        if not up:
            raise BrowserWorkflowError(
                f"{kind.name} is not running with its control port ({detail}). Say 'open a browser' first.")
        return detail

    def _tabs(self, kind: BrowserKind) -> list[TabView]:
        return [TabView(number=i, id=t.id, url=safe_url(t.url), title=(t.title or "")[:120])
                for i, t in enumerate(self._plane.endpoint.pages(kind), start=1)]

    def _tab_id(self, kind: BrowserKind, args: dict[str, Any]) -> str | None:
        """`tab` is a list number (1, 2...) or a tab id from browser.tabs. None means the active tab."""
        wanted = str(args.get("tab") or "").strip()
        if not wanted:
            return None
        tabs = self._tabs(kind)
        if wanted.isdigit() and 1 <= int(wanted) <= len(tabs):
            return tabs[int(wanted) - 1].id
        if any(t.id == wanted for t in tabs):
            return wanted
        raise BrowserWorkflowError(f"no open tab {wanted!r} in {kind.name}; there are {len(tabs)}")

    def _page(self, kind: BrowserKind, args: dict[str, Any]):
        self._up(kind)
        return self._plane.attacher.attach(kind, self._tab_id(kind, args))


class Launch(BrowserOperation):
    """Phase 1 of browser.open: make sure Chrome is running where the person can see it.

    Already running and visible (or Windows can't say): leave it alone. Running but hidden in session 0
    (the interop launch of earlier versions): close that one -- nobody can see or use it -- and start a
    visible one. Not running: start it through the desktop starter and wait for its control port."""
    name = "browser_launch"
    description = "browser.open, phase 1: start Chrome on the signed-in person's desktop (replacing a hidden one)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        url = checked_url(args["url"]) if args.get("url") else "about:blank"
        up, detail = self._plane.endpoint.reachable(kind)
        replaced = False
        linked, why = self._plane.link.ensure()                 # PowerShell and chrome.exe both need it
        if up and linked and self._plane.sessions is not None:
            where = self._plane.sessions.placement(kind)
            if where.known and where.found and not where.visible:
                self._plane.endpoint.shutdown(kind)
                if not self._wait(kind, want_up=False, seconds=10.0):
                    return refused(f"{kind.name} is running in hidden Windows session {where.session}, and it would not close "
                                   "so a visible one could start.", browser=kind.name, verified=False, cause="hidden_session")
                up, replaced = False, True
        if up:
            return ok(f"{kind.name} is already running ({detail}).", browser=kind.name, version=detail,
                      started=False, replaced_hidden=False)
        if not linked:
            return refused(why, browser=kind.name, started=False, verified=False, cause="windows_interop")
        launched, reason = self._plane.starter.start(kind, url)
        if not launched:
            return refused(f"I could not start {kind.name}: {reason}", browser=kind.name, verified=False, cause="launch")
        if not self._wait(kind, want_up=True, seconds=self._plane.ready_seconds):
            _, detail = self._plane.endpoint.reachable(kind)
            return refused(f"I started {kind.name} but could not control it: "
                           f"{self._plane.link.explain_unreachable(kind.port)} ({detail})",
                           browser=kind.name, started=True, verified=False, cause="control_port")
        _, detail = self._plane.endpoint.reachable(kind)
        return ok(f"{kind.name} started ({detail})" + (", replacing a hidden one." if replaced else "."),
                  browser=kind.name, version=detail, started=True, replaced_hidden=replaced)

    def _wait(self, kind: BrowserKind, want_up: bool, seconds: float) -> bool:
        deadline = self._plane.clock.monotonic() + seconds
        while True:
            if self._plane.endpoint.reachable(kind)[0] == want_up:
                return True
            if self._plane.clock.monotonic() >= deadline:
                return False
            self._plane.clock.sleep(0.5)


class Visibility(BrowserOperation):
    """Phase 2 of browser.open: Windows confirms the Chrome on the control port is on a desktop a person can see.
    "The port answers" only proves it runs; a Chrome in session 0 answers too and is never seen."""
    name = "browser_visibility"
    description = "browser.open, phase 2: confirm with Windows that Chrome is on a visible desktop, not hidden session 0"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        if self._plane.sessions is None:
            return refused(f"I can't check where {kind.name} opened, so I won't say you can see it.",
                           browser=kind.name, verified=False, cause="visibility_unknown")
        linked, why = self._plane.link.ensure()
        where = self._plane.sessions.placement(kind)          # may be remembered for this Chrome: no Windows call
        if not linked and not where.known:
            return refused(f"{kind.name} is running, but I couldn't ask Windows whether it is on your screen: {why}",
                           browser=kind.name, verified=False, cause="visibility_unknown")
        facts = dict(browser=kind.name, pid=where.pid, session=where.session, desktops=where.desktops)
        if not where.known:
            return refused(f"{kind.name} is running, but I couldn't confirm it is on your screen: {where.reason}",
                           verified=False, cause="visibility_unknown", **facts)
        if not where.found:
            return refused(f"Windows shows no {kind.name} owning control port {kind.port}.",
                           verified=False, cause="visibility_unknown", **facts)
        if not where.visible:
            hidden = "the hidden services session (0)" if where.session == 0 else f"session {where.session}, which has no desktop"
            return refused(f"{kind.name} is running but not on your screen: Windows has it in {hidden}; "
                           f"desktops are in session(s) {where.desktops or 'none'}.", verified=False, cause="hidden_session", **facts)
        return ok(f"{kind.name} is on the desktop in Windows session {where.session}.", visible=True, **facts)


class Open(BrowserOperation):
    """Phase 3 of browser.open: add the asked-for tab to a Chrome that was already running, and verify the tabs.
    The catalog runs it after Launch and Visibility (an OperationSequence), whose data it reads."""
    name = "browser_open"
    description = "browser.open: start Chrome on the Windows desktop with a control port (or add a tab)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        detail = self._up(kind)
        started = bool(args.get("started"))
        if args.get("url") and not started:                  # already running: add a tab, touch nothing else
            self._plane.endpoint.open_page(kind, checked_url(args["url"]))
        tabs = self._tabs(kind)
        if not tabs:
            return refused(f"{kind.name} answered but shows no open page.", browser=kind.name, verified=False)
        what = "started" if started else "was already running"
        if args.get("replaced_hidden"):
            what = "started (a hidden copy was closed first)"
        where = f" in Windows session {args['session']}" if args.get("visible") else ""
        return ok(f"{kind.name} {what} on your desktop{where} ({detail}); {len(tabs)} tab(s) open.",
                  browser=kind.name, version=detail, started=started, verified=True, tabs=[t.model_dump() for t in tabs])


class Navigate(BrowserOperation):
    name = "browser_navigate"
    description = "browser.navigate: go to an http(s) URL in the chosen (or active) tab"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind, url = self._kind(args), checked_url(args.get("url"))
        page = self._page(kind, args)
        try:
            page.navigate(url)
            now, title = page.current_url, page.title
        finally:
            page.release()
        landed = urlparse(now).netloc == urlparse(url).netloc
        line = f"Now at {safe_url(now)} ({title[:80]!r})." if landed else \
               f"Asked for {safe_url(url)} but the tab is at {safe_url(now)} ({title[:80]!r}) -- it was redirected."
        return ok(line, browser=kind.name, url=safe_url(now), title=title[:120], redirected=not landed, verified=True)


class Refresh(BrowserOperation):
    name = "browser_refresh"
    description = "browser.refresh: reload the chosen (or active) tab"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        page = self._page(kind, args)
        try:
            before = safe_url(page.current_url)
            page.reload()
            now, title = safe_url(page.current_url), page.title
        finally:
            page.release()
        return ok(f"Reloaded {now} ({title[:80]!r}).", browser=kind.name, url=now, title=title[:120],
                  was=before, verified=True)


class Tabs(BrowserOperation):
    name = "browser_tabs"
    description = "browser.tabs: list the open tabs (number, id, URL without query, title)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        self._up(kind)
        tabs = self._tabs(kind)
        lines = "\n".join(f"{t.number}. {t.title or '(untitled)'} -- {t.url}" for t in tabs)
        return ok(f"{len(tabs)} tab(s) in {kind.name}:\n{lines}" if tabs else f"{kind.name} has no open page.",
                  browser=kind.name, tabs=[t.model_dump() for t in tabs], verified=True)


class SelectTab(BrowserOperation):
    name = "browser_select_tab"
    description = "browser.select_tab: bring one tab (by number or id) to the front"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        if not args.get("tab"):
            return refused("Say which tab: its number from browser.tabs, or its id.")
        self._up(kind)
        wanted = self._tab_id(kind, args)
        self._plane.endpoint.activate(kind, wanted or "")
        page = self._plane.attacher.attach(kind, wanted)
        try:
            url, title = safe_url(page.current_url), page.title
            self._plane.clock.sleep(0.3)
            in_front = page.is_foreground()                  # the page's own visibilityState, not the list order
        finally:
            page.release()
        if not in_front:
            return refused("I asked for that tab, but the browser still lists another one in front.",
                           browser=kind.name, tab=wanted, verified=False)
        return ok(f"Tab {args['tab']} is now in front: {title[:80]!r} at {url}.", browser=kind.name, tab=wanted,
                  url=url, title=title[:120], verified=True)


class Click(BrowserOperation):
    name = "browser_click"
    description = "browser.click: press one element, named by text, label or css. Consequential ones need confirm=true"
    destructive = True

    def __init__(self, plane: ControlPlane) -> None:
        super().__init__(plane)
        self._resolver = RankedElementResolver(default_strategy_registry())
        self._classifier = KeywordConsequenceClassifier()

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        candidates = locators_from(args)
        hint = " ".join(str(args.get(k) or "") for k in ("text", "label"))
        why = self._classifier.reason(RecordedAction(action_type="click", metadata={"text": hint}))
        if why and not args.get("confirm"):
            return refused(f"I did not click: {why}. Ask EG, then repeat with confirm=true.", browser=kind.name,
                           needs_confirmation=True, verified=False)
        page = self._page(kind, args)
        try:
            element, resolution = self._resolver.resolve(page, candidates)
            if element is None:
                tried = ", ".join(f"{a.label}: {a.outcome}" for a in resolution.attempts)
                return refused(f"I could not find one clickable element for that ({tried})."
                               + self._dropdown_hint(page, hint)
                               + self._targeting_hint(page, resolution, hint),
                               browser=kind.name, verified=False)
            if element.tag_name == "input" and (element.get_attribute("type") or "").lower() == "password":
                return refused("That is a password field; I do not touch those.", browser=kind.name, verified=False)
            # Pressing this saves a file, and a click cannot keep one: Chrome writes a download only
            # when a folder has been set for the session, which browser.download does and this does not.
            # Clicking anyway would report an honest "Clicked 'x.tif'." over an empty folder -- the one
            # failure that looks exactly like a success.
            saves = saved_extension(element)
            if saves:
                return refused(f"That saves a file (.{saves}), and a click cannot keep it -- Chrome only writes a "
                               "download when a folder has been set for the session. Use browser.download with the "
                               "same text, label or css and it saves the file and reports its name and size.",
                               browser=kind.name, saves_file=saves, use_instead="browser.download", verified=False)
            label = (element.text or "")[:60]
            before = page.current_url
            element.click()
            after = page.current_url
        finally:
            page.release()
        moved = safe_url(after) if after != before else ""
        return ok(f"Clicked {label!r}" + (f"; the page moved to {moved}." if moved else "."),
                  browser=kind.name, clicked=label, moved_to=moved, winner=resolution.winner, verified=True)

    @staticmethod
    def _targeting_hint(page, resolution, wanted: str) -> str:
        """Why the resolver refused, and the one operation that ends the guessing.

        Three outcomes are all the same mistake, so they get the same answer. "ambiguous" means the
        words name several things on the screen -- a heading, the row it sits in, and the control, all
        carrying the same caption. "hidden" and "zero_size" mean the thing is in the page but not on it:
        a collapsed section or a closed menu, so what has to be pressed first is whatever reveals it.
        In every case the fix is to read the controls and aim with the selector one of them carries,
        never to take the first match -- which would press a heading and report success.
        """
        blocked = {a.outcome for a in resolution.attempts} & {"ambiguous", "hidden", "zero_size"}
        if not blocked:
            return ""
        asked = " ".join(wanted.split()).rstrip(":*").strip().casefold()
        named = []
        if asked:
            try:
                found = page.form_controls(MAX_CONTROLS)
            except Exception:
                found = {}
            for control in (found or {}).get("controls") or []:
                said = " ".join(f"{control.get('label') or ''} {control.get('text') or ''}".split()).casefold()
                if control.get("css") and asked in said:
                    named.append(f"{control['css']} ({(control.get('label') or control.get('text') or '')[:40]!r}"
                                 + ("" if control.get("visible") else ", not on the screen") + ")")
        why = (" Those words name more than one thing on the screen."
               if "ambiguous" in blocked else
               " What matched is in the page but not on the screen, so it is inside a collapsed section or a"
               " closed menu; press whatever opens that first.")
        advice = (f" browser.form_controls lists these candidates: {'; '.join(named[:6])}."
                  " Repeat with --css=<one of those>." if named else
                  " Run browser.form_controls and repeat with --css taken from the control you mean.")
        return why + advice

    @staticmethod
    def _dropdown_hint(page, wanted: str) -> str:
        """The commonest reason a click finds nothing: what was asked for is an item inside a dropdown.
        Chrome draws the open list with the OS, so those items have no box on the page and no resolver
        can reach them. Say so, and name the operation that can."""
        asked = " ".join(wanted.split()).rstrip(":*").strip().casefold()
        if not asked:
            return ""
        try:
            found = page.form_controls(MAX_CONTROLS)
        except Exception:
            return ""
        for control in (found or {}).get("controls") or []:
            for option in control.get("options") or []:
                said = " ".join(str(option.get("text") or "").split()).casefold()
                if said and (said == asked or asked in said):
                    return (f" {option.get('text')!r} is an item in the dropdown {control.get('css')} -- an item in a "
                            "list is never clicked (Chrome draws the open list outside the page). "
                            f"Use browser.select_option --css={control.get('css')} --option-text={option.get('text')!r}.")
        return ""


class Read(BrowserOperation):
    name = "browser_read"
    description = ("browser.read: the visible text of the page, and which of it is highlighted or blinking "
                   "(never form values, cookies or storage)")

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        page = self._page(kind, args)
        probe = getattr(page, "highlights", None)
        try:
            text, url, title = page.body_text(), safe_url(page.current_url), page.title
            found = probe("", 40, int(args.get("sample_ms") or 1500)) if probe else {}
        finally:
            page.release()
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        clipped = len(text) > MAX_READ_CHARS
        states = classify_page(found or {})
        lit = blinking(states)
        said = f"{title[:80]!r} at {url}: {len(text)} characters of visible text."
        if states:
            said += (f" {len(states)} highlighted item{'s' if len(states) != 1 else ''}"
                     + (f"; blinking: {'; '.join(repr(s.text[:120]) for s in lit)}." if lit else ", none blinking."))
        return ok(said, browser=kind.name, url=url, title=title[:120], text=text[:MAX_READ_CHARS], truncated=clipped,
                  highlights=[s.model_dump() for s in states], blinking=[s.text for s in lit], verified=True)


class Screenshot(BrowserOperation):
    name = "browser_screenshot"
    description = "browser.screenshot: save a PNG of the visible page and say where"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        page = self._page(kind, args)
        self._plane.shots.mkdir(parents=True, exist_ok=True)
        path = self._plane.shots / f"{kind.name}_{int(self._plane.clock.monotonic() * 1000)}.png"
        try:
            saved = page.capture_png(str(path))
            url = safe_url(page.current_url)
        finally:
            page.release()
        if not saved or not path.exists() or path.stat().st_size == 0:
            return refused("The browser returned no image.", browser=kind.name, verified=False)
        return ok(f"Saved a {path.stat().st_size}-byte screenshot of {url}.", browser=kind.name, path=str(path),
                  bytes=path.stat().st_size, url=url, verified=True)


class Status(BrowserOperation):
    name = "browser_status"
    description = "browser.status: is Chrome up, which version, how many tabs"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        names = [args.get("browser") or "chrome"]
        report, lines = [], []
        for name in names:
            kind = self._plane.catalog.kind(name)
            up, detail = self._plane.endpoint.reachable(kind)
            tabs = len(self._plane.endpoint.pages(kind)) if up else 0
            report.append({"browser": kind.name, "running": up, "version": detail if up else "", "tabs": tabs,
                           "port": kind.port, "reason": "" if up else detail})
            lines.append(f"{kind.name}: " + (f"running ({detail}), {tabs} tab(s)" if up else f"not running ({detail})"))
        return ok("; ".join(lines), browsers=report, verified=True)


class Close(BrowserOperation):
    name = "browser_close"
    description = "browser.close: close ONE explicitly chosen tab; it never quits the browser or touches other windows"
    destructive = True

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        if not args.get("tab"):
            return refused("Say exactly which tab to close: its number or id from browser.tabs. I never close by guessing.")
        self._up(kind)
        target = self._tab_id(kind, args)
        before = self._tabs(kind)
        if len(before) == 1:
            return refused("That is the only open tab; closing it would quit the browser, so I left it.",
                           browser=kind.name, verified=False)
        self._plane.endpoint.close_page(kind, target or "")
        self._plane.clock.sleep(0.3)
        after = self._tabs(kind)
        gone = all(t.id != target for t in after)
        if not gone:
            return refused("I asked the browser to close that tab, but it is still listed.", browser=kind.name, verified=False)
        return ok(f"Closed tab {args['tab']}; {len(after)} tab(s) remain.", browser=kind.name, closed=target,
                  remaining=len(after), verified=True)


# ---- the form operations: see what is on the page, choose from a list, type in a field ----------------------
#
# browser.click cannot work a dropdown and never could: Chrome gives an <option> a 0x0 box (the open
# list is drawn by Windows, not by the page), so every element resolver rejects them as zero-size.
# A list is not clicked, it is told its new value -- these operations resolve the <select> itself.


class FormControls(BrowserOperation):
    """What the page offers a person to operate, so the next call names a control instead of guessing one."""
    name = "browser_form_controls"
    description = ("browser.form_controls: the page's fields, dropdowns (with every option and the one chosen) "
                   "and buttons, each with a stable selector and whether it can be operated")

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        page = self._page(kind, args)
        try:
            found = page.form_controls(self._limit(args))
            url, title = safe_url(page.current_url), page.title
        finally:
            page.release()
        controls = [FormControlView.model_validate(c) for c in (found or {}).get("controls") or []]
        total = int((found or {}).get("total") or len(controls))
        if not controls:
            return ok(f"{title[:80]!r} at {url} has no fields, dropdowns or buttons on it.", browser=kind.name,
                      url=url, title=title[:120], total=0, shown=0, controls=[], listing="", verified=True)
        shown = sum(1 for c in controls if c.visible)
        listing = "\n".join(self._describe(c) for c in controls)
        more = f" ({total - len(controls)} more not listed; raise limit)" if total > len(controls) else ""
        return ok(f"{title[:80]!r} at {url}: {total} control(s), {shown} of them on the screen{more}.\n{listing}",
                  browser=kind.name, url=url, title=title[:120], total=total, shown=len(controls),
                  controls=[c.model_dump() for c in controls], listing=listing, verified=True)

    @staticmethod
    def _limit(args: dict[str, Any]) -> int:
        try:
            wanted = int(args.get("limit") or 60)
        except (TypeError, ValueError):
            raise BrowserWorkflowError(f"--limit takes a number, not {args.get('limit')!r}")
        return max(1, min(wanted, MAX_CONTROLS))

    @staticmethod
    def _describe(control: FormControlView) -> str:
        """One line a person (or a model choosing its next call) can act on without reading the JSON."""
        state = [] if control.visible else ["not on the screen"]
        state += [] if control.enabled else ["disabled"]
        state += ["read-only"] if control.readonly else []
        state += ["required"] if control.required else []
        state += ["holds something"] if control.filled else []
        state += ["NEVER filled or read: it may be a credential"] if control.secret else []
        state += ([f"now {control.selected!r}" if control.selected else "nothing chosen yet",
                   "the site draws this list itself: select_option opens it, its items are not in the page yet"]
                  if control.site_drawn else [])
        name = control.label or control.text or control.name or control.id or "(unnamed)"
        line = f"[{control.role}] {name!r} css={control.css}" + (f" -- {', '.join(state)}" if state else "")
        if control.options:
            items = " | ".join(f"{o.text!r}=value {o.value!r}" + (" (disabled)" if o.disabled else "")
                               for o in control.options[:30])
            line += (f"\n    {len(control.options)} option(s), now {control.selected or 'nothing'!r}: {items}"
                     + (" | ..." if len(control.options) > 30 else ""))
        return line


class FormOperation(BrowserOperation):
    """Shared by select_option and fill: find the one named control, and never touch a credential field.

    Aimed by --css or --label only. The label match forgives a form's punctuation ("Record Type:" is
    "Record Type"), which the plain click locator does not, and which is why a label that is plainly on
    the screen used to find nothing.
    """
    destructive = True

    def __init__(self, plane: ControlPlane) -> None:
        super().__init__(plane)
        self._resolver = RankedElementResolver(form_strategy_registry())
        self._guard = SecretFieldGuard()

    def _control(self, page, args: dict[str, Any], **facts: Any):
        """(element, None) when exactly one usable control is named; (None, the refusal) otherwise."""
        element, resolution = self._resolver.resolve(page, form_locators_from(args))
        if element is None:
            return None, refused(self._why_not(resolution), verified=False, cause=self._cause(resolution),
                                 attempts=[a.model_dump() for a in resolution.attempts], **facts)
        why = self._guard.reason(element)
        if why:
            return None, refused(f"I did not touch that field: {why}.", verified=False, cause="credential_field", **facts)
        return element, None

    @staticmethod
    def _cause(resolution) -> str:
        outcomes = {a.outcome for a in resolution.attempts}
        for outcome, cause in (("disabled", "disabled"), ("ambiguous", "ambiguous_control"),
                               ("hidden", "not_visible"), ("zero_size", "not_visible")):
            if outcome in outcomes:
                return cause
        return "control_not_found"

    def _why_not(self, resolution) -> str:
        tried = ", ".join(f"{a.label}: {a.outcome}" + (f" ({a.count})" if a.count > 1 else "")
                          for a in resolution.attempts)
        advice = {
            "disabled": "That control is on the page but disabled, so nothing can be done with it yet -- "
                        "something earlier on the form may have to be set first.",
            "ambiguous": "More than one control matches that. Run browser.form_controls and pass the css it gives.",
            "hidden": "That control is on the page but not showing, so it cannot be operated.",
            "zero_size": "That control is on the page but not showing, so it cannot be operated.",
        }
        outcomes = {a.outcome for a in resolution.attempts}
        extra = next((advice[o] for o in ("disabled", "ambiguous", "hidden", "zero_size") if o in outcomes),
                     "Run browser.form_controls to see what is on the page, then pass the css it gives.")
        return f"I could not find one form control for that ({tried}). {extra}"


class SelectOption(FormOperation):
    """browser.select_option: choose an item in a dropdown and prove the list took it."""
    name = "browser_select_option"
    description = ("browser.select_option: choose one item in a dropdown by what it says (option_text) or by its "
                   "value, then read the list back to prove it took")

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        wanted_value = str(args.get("value") or "")
        wanted_text = str(args.get("option_text") or "")
        page = self._page(kind, args)
        try:
            facts = dict(browser=kind.name, url=safe_url(page.current_url))
            element, no = self._control(page, args, **facts)
            if no is not None:
                return no
            if element.tag_name != "select":
                return self._site_drawn(page, element, wanted_value, wanted_text, **facts)
            options = [OptionView.model_validate(o) for o in element.options()]
            option, why = OptionMatcher().choose(options, wanted_value, wanted_text)
            if option is None:
                return refused(f"I did not change the list: {why}", verified=False, cause="no_such_option",
                               options=[o.model_dump() for o in options], **facts)
            landed = element.choose(option.value)
            chosen, note = self._read_back(page, args)
        finally:
            page.release()
        asked = f"{option.text!r} (value {option.value!r})"
        if landed != option.value:
            return refused(f"The list would not take {asked}: it still holds {landed!r}.", verified=False,
                           cause="not_accepted", wanted=option.value, selected_value=landed or "", **facts)
        if chosen is not None and chosen.value != option.value:
            return refused(f"I chose {asked}, but the page put the list back to {chosen.text!r} -- something on the "
                           "form overrode it.", verified=False, cause="did_not_stick",
                           wanted=option.value, selected_value=chosen.value, **facts)
        confirmed = "the page confirms it" if chosen is not None else note
        return ok(f"Chose {asked} in that dropdown; {confirmed}.", selected=option.text, selected_value=option.value,
                  position=option.position, note=note, verified=True, **facts)

    def _site_drawn(self, page, element, wanted_value: str, wanted_text: str, **facts: Any) -> OperationOutcome:
        """A list the site draws itself, which is the opposite act to a native one and the same
        operation to the caller: its items are nodes the page lays out, so they have real boxes and
        are clicked -- but only after the control is opened, because until then they are not in the
        page at all. That is why browser.click on the item alone could never work either."""
        if not looks_site_drawn(element):
            return refused(f"That is a <{element.tag_name}> with nothing about it that says it holds a list "
                           "(no combobox role, no popup, no list of its own). Run browser.form_controls to see "
                           "what is on the page.", verified=False, cause="not_a_dropdown", **facts)
        widget = WidgetDropdown()
        items = widget.open(page, element)
        options = widget.read(items)
        if not options:
            return refused("I opened that dropdown and it put no items on the page, so there was nothing to "
                           "choose. It may need a word typed into it first (browser.fill) before it offers any.",
                           verified=False, cause="no_options_shown", site_drawn=True, **facts)
        item, option, why = widget.choose(items, options, wanted_value, wanted_text)
        if option is None:
            return refused(f"I did not change the list: {why}", verified=False, cause="no_such_option",
                           options=[o.model_dump() for o in options], site_drawn=True, **facts)
        item.click()
        showing = self._showing(page, element)
        found = dict(selected=option.text, selected_value=option.value, position=option.position,
                     site_drawn=True, **facts)
        if not showing:
            return ok(f"I clicked {option.text!r} in that site-drawn dropdown, but the control shows no text of "
                      "its own, so I cannot prove it took. Confirm with browser.read.", verified=False,
                      note="nothing to read the choice back from", **found)
        if not same_characters(option.text, showing) and option.text.casefold() not in showing.casefold():
            return refused(f"I clicked {option.text!r} in that site-drawn dropdown, but it is showing "
                           f"{showing!r} instead.", verified=False, cause="did_not_stick",
                           note=f"showing {showing!r}", **found)
        return ok(f"Chose {option.text!r} in that site-drawn dropdown; it now shows {showing!r}.",
                  verified=True, note=f"showing {showing!r}", **found)

    @staticmethod
    def _showing(page, element) -> str:
        """What a site's own dropdown says it holds now: the text in the control, or the item it
        points at with aria-activedescendant. Empty means there is nothing to read it back from."""
        try:
            said = " ".join((element.value() or element.text or "").split())[:160]
        except BrowserWorkflowError:
            said = ""
        if said:
            return said
        pointed = (element.get_attribute("aria-activedescendant") or "").strip()
        if not pointed or '"' in pointed:
            return ""
        for node in page.find(f'[id="{pointed}"]'):
            words = " ".join((node.text or "").split())[:160]
            if words:
                return words
        return ""

    def _read_back(self, page, args: dict[str, Any]) -> tuple[OptionView | None, str]:
        """Ask the page again what the list holds. Plenty of dropdowns post the form back when they
        change, which replaces the document and takes the element with it: that is said, not guessed at.
        The value `choose` returns is already a read of the list after its change event, so a reload
        does not leave the result unverified -- it only leaves the second opinion unavailable."""
        try:
            element, _ = self._resolver.resolve(page, form_locators_from(args))
            if element is None:
                return None, "choosing it reloaded the form, so the list itself is no longer there to re-read"
            chosen = next((o for o in [OptionView.model_validate(o) for o in element.options()] if o.selected), None)
            if chosen is None:
                return None, "the list reports nothing selected afterwards"
            return chosen, ""
        except BrowserWorkflowError as exc:
            return None, f"the page changed while I read the list back ({exc})"


class Fill(FormOperation):
    """browser.fill: type into one ordinary field and prove the field holds it."""
    name = "browser_fill"
    description = ("browser.fill: type text into one ordinary field (--text is what to type), then read the field "
                   "back to prove it took. Never a password or other credential field")
    #: Typing is tried first and the field is told its value only if the keys got nowhere, so a field
    #: with its own format (a date input) or its own mask (a phone field) is filled rather than refused.
    _check = TypedValueCheck()

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        if args.get("text") is None:
            return refused("Say what to type: --text. (For browser.fill, text is the words to type; the field "
                           "itself is named by --css or --label.)")
        wanted = str(args["text"])
        page = self._page(kind, args)
        try:
            facts = dict(browser=kind.name, url=safe_url(page.current_url))
            element, no = self._control(page, args, **facts)
            if no is not None:
                return no
            unusable = self._unusable(element)
            if unusable:
                return refused(unusable[0], verified=False, cause=unusable[1], **facts)
            value = element.fill(wanted)
            verdict = self._check.verdict(wanted, value, element)
            # What the caller called it, if they named it: a form's aria-label is often just the id again.
            where = str(args.get("label") or args.get("css") or element.get_attribute("name")
                        or element.get_attribute("id") or element.tag_name)[:80]
        finally:
            page.release()
        if verdict.ok and not verdict.note:
            return ok(f"Typed {wanted!r} into {where!r}; the field reads it back.", typed=wanted, value=value,
                      field=where, note="", verified=True, **facts)
        if verdict.ok:
            return ok(f"Typed {wanted!r} into {where!r}: {verdict.note}.", typed=wanted, value=value,
                      field=where, note=verdict.note, verified=True, **facts)
        return refused(f"I typed {wanted!r} into {where!r}, but {verdict.note}. Nothing was submitted.",
                       typed=wanted, value=value, field=where, note=verdict.note, verified=False,
                       cause=verdict.cause, **facts)

    @staticmethod
    def _unusable(element) -> tuple[str, str] | None:
        """Why this control is not something a person types words into."""
        tag = element.tag_name
        kind = (element.get_attribute("type") or "").lower()
        editable = element.get_attribute("contenteditable")
        if element.get_attribute("readonly") is not None or (element.get_attribute("aria-readonly") or "") == "true":
            return "That field is read-only: the page does not let anyone type in it.", "readonly"
        if tag == "select":
            return "That is a dropdown -- use browser.select_option for it.", "not_editable"
        if tag == "input" and kind in ("checkbox", "radio", "submit", "button", "reset", "image"):
            return f"That is a {kind} input, not a text field -- use browser.click for it.", "not_editable"
        if tag == "input" and kind == "file":
            return "That is a file chooser; I never put files into a web page.", "not_editable"
        if tag == "input" and kind not in TYPEABLE:
            return f"I do not type into a {kind!r} input.", "not_editable"
        if tag not in ("input", "textarea") and editable not in ("", "true"):
            return f"A <{tag}> is not a field a person types into.", "not_editable"
        return None


# ---- browser.dashboard_notes: open Chrome, go to the dashboard, press NOTES, confirm it is showing -------------

DASHBOARD_URL = "https://desktop-2obsqmc.tailb8fc54.ts.net/"
NOTES_TAB_CSS = "button.tab[data-nav='main'][data-target='notes']"    # the dashboard's own NOTES button


class NotesRequest(BrowserOperation):
    """Phase 1 of browser.dashboard_notes: fix the destination. Nothing the caller sends can change it."""
    name = "browser_notes_request"
    description = "browser.dashboard_notes, phase 1: the destination is the ROL dashboard's NOTES tab"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        return ok(f"Going to the Notes tab of {DASHBOARD_URL} in {kind.name}.", browser=kind.name,
                  url=DASHBOARD_URL, css=NOTES_TAB_CSS, text="", label="", locators=[])


class DashboardTab(BrowserOperation):
    """Phase 4: the dashboard tab -- an open one is reused (no pile of duplicate tabs), else one is opened --
    brought to the front and waited on until the page shows its NOTES button."""
    name = "browser_dashboard_tab"
    description = "browser.dashboard_notes, phase 4: find or open the dashboard tab and wait for it to load"
    wait_seconds = 20.0

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        self._up(kind)
        host = urlparse(DASHBOARD_URL).netloc
        tab = next((t for t in self._plane.endpoint.pages(kind) if urlparse(t.url).netloc == host), None)
        opened = tab is None
        if opened:
            tab = self._plane.endpoint.open_page(kind, DASHBOARD_URL)
        self._plane.endpoint.activate(kind, tab.id)
        deadline = self._plane.clock.monotonic() + self.wait_seconds
        while True:
            page = self._plane.attacher.attach(kind, tab.id)
            try:
                loaded, url, title = bool(page.find(NOTES_TAB_CSS)), page.current_url, page.title
            finally:
                page.release()
            if loaded:
                break
            if self._plane.clock.monotonic() >= deadline:
                return refused(f"The dashboard tab is at {safe_url(url)} ({title[:60]!r}) but never showed its NOTES tab "
                               f"within {int(self.wait_seconds)}s -- the dashboard may be down or still loading.",
                               browser=kind.name, tab=tab.id, verified=False, cause="dashboard_not_loaded")
            self._plane.clock.sleep(1.0)
        return ok(("Opened" if opened else "Found") + f" the dashboard tab at {safe_url(url)} and brought it to the front.",
                  browser=kind.name, tab=tab.id, opened_tab=opened, url=safe_url(url))


class NotesVerify(BrowserOperation):
    """Phase 6: read the page back. Success means the dashboard's own NOTES button is the active tab, not that a
    click was sent."""
    name = "browser_notes_verify"
    description = "browser.dashboard_notes, phase 6: read the page and confirm the NOTES tab is the active one"
    wait_seconds = 5.0

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        self._up(kind)
        deadline = self._plane.clock.monotonic() + self.wait_seconds
        while True:
            page = self._plane.attacher.attach(kind, args.get("tab") or None)
            try:
                found = page.find(NOTES_TAB_CSS)
                classes = (found[0].get_attribute("class") or "").split() if found else []
                url, title, front = safe_url(page.current_url), page.title, page.is_foreground()
                text = re.sub(r"\s+", " ", page.body_text()).strip() if "active" in classes else ""
            finally:
                page.release()
            if "active" in classes or self._plane.clock.monotonic() >= deadline:
                break
            self._plane.clock.sleep(0.5)
        where = f" on your desktop in Windows session {args['session']}" if args.get("visible") else ""
        if "active" not in classes:
            return refused(f"I pressed NOTES, but the page at {url} does not show the Notes tab as active "
                           f"(its classes: {' '.join(classes) or 'button not found'}).", browser=kind.name,
                           url=url, title=title[:120], verified=False, cause="notes_not_active")
        return ok(f"Chrome is open{where} on the dashboard ({url}) with the NOTES tab showing.",
                  browser=kind.name, url=url, title=title[:120], notes_active=True, in_front=front,
                  excerpt=text[:600], verified=True)
