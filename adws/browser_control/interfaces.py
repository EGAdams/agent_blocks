"""What the operations need from the machine, as Protocols (say "interface", never "port")."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from cdp_browser.interfaces import CdpTarget
from .models import BrowserKind, BrowserPlacement


class IBrowserCatalog(Protocol):
    def kind(self, name: str | None) -> BrowserKind: ...        # raises BrowserWorkflowError for an unknown browser


class IBrowserStarter(Protocol):
    """Starts the browser with its DevTools port, detached. Never blocks, never raises."""
    def start(self, kind: BrowserKind, url: str) -> tuple[bool, str]: ...


class IBrowserEndpoint(Protocol):
    """The browser's DevTools side, on loopback only."""
    def reachable(self, kind: BrowserKind) -> tuple[bool, str]: ...    # (answers, "Chrome/xxx" or why not)
    def pages(self, kind: BrowserKind) -> list[CdpTarget]: ...
    def open_page(self, kind: BrowserKind, url: str) -> CdpTarget: ...
    def activate(self, kind: BrowserKind, target_id: str) -> None: ...
    def close_page(self, kind: BrowserKind, target_id: str) -> None: ...
    def shutdown(self, kind: BrowserKind) -> None: ...                 # close the whole browser (Browser.close)


class IFormElement(Protocol):
    """One form control, as the two form operations need it.

    Beyond browser_workflows' IWorkflowElement (text, tag, attributes, displayed, enabled) a
    control is asked for its options and told its new value or text. An `<option>` is never
    clicked -- Chrome gives it no box -- so `choose` is how a native dropdown is worked. A
    dropdown the site draws itself is clicked open instead, and `value` is how its choice reads back.
    """
    @property
    def text(self) -> str: ...
    @property
    def tag_name(self) -> str: ...
    def get_attribute(self, name: str) -> str | None: ...
    def is_displayed(self) -> bool: ...
    def is_enabled(self) -> bool: ...
    def click(self) -> None: ...                            # a site's own dropdown is opened, and its items clicked
    def value(self) -> str: ...                             # what the control shows now (never a secret field's)
    def options(self) -> list[dict[str, Any]]: ...          # a dropdown's items, in order; empty for anything else
    def choose(self, value: str) -> str | None: ...         # select the option with this exact value, firing input+change
    def fill(self, text: str) -> str: ...                   # replace the field's text; returns the value afterwards


class IPageSession(Protocol):
    """One attached tab. The CDP browser satisfies this as it stands."""
    @property
    def current_url(self) -> str: ...
    @property
    def title(self) -> str: ...
    def navigate(self, url: str) -> None: ...
    def reload(self) -> None: ...
    def body_text(self) -> str: ...
    def is_foreground(self) -> bool: ...
    def capture_png(self, path: str) -> bool: ...
    def find(self, css_selector: str) -> list: ...
    def form_controls(self, limit: int = 60) -> dict[str, Any]: ...   # {"total": n, "controls": [...]}, no field's text
    def release(self) -> None: ...


class IPageAttacher(Protocol):
    def attach(self, kind: BrowserKind, tab_id: str | None) -> IPageSession: ...


class IClock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class IWindowsLink(Protocol):
    """Can this process start Windows programs, and why can't it see what they open?"""
    def ensure(self) -> tuple[bool, str]: ...
    def explain_unreachable(self, port: int) -> str: ...


class IWindowSessions(Protocol):
    """Which Windows session the debug browser is in, and which sessions a person can see. Never raises."""
    def placement(self, kind: BrowserKind) -> BrowserPlacement: ...


class IDownloadFolder(Protocol):
    """Where Chrome should save what it downloads. The path is on **Chrome's** machine, so from WSL
    this is a Windows path -- handing Chrome the /mnt/c spelling of it saves nothing anywhere."""
    def resolve(self) -> str: ...                           # raises BrowserWorkflowError if it cannot be found


class IDownloadWatcher(Protocol):
    """That same folder as *this* machine sees it. `wait_new` returns only files Chrome has finished
    writing (no .crdownload beside them), and an empty list when nothing arrived in time -- which is
    what lets browser.download refuse to call a click a download."""
    def before(self) -> set[str]: ...
    def wait_new(self, before: set[str], timeout_seconds: float = 30.0) -> list[Path]: ...


class IDownloadVerifier(Protocol):
    """One sentence naming what is wrong with a file that landed, or "" when nothing is."""
    def problem(self, path: Path) -> str: ...


class IDownloadingPage(IPageSession, Protocol):
    """A tab that can also be told where Chrome saves. `CdpWorkflowBrowser` satisfies it; a page that
    does not is refused by browser.download rather than clicked and hoped over.

    This is separate from IPageSession on purpose: the other twelve operations must not acquire the
    ability to write files to EG's disk just because one of them needed it."""
    def configure_downloads(self, directory: str) -> None: ...
