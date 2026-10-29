"""The browser as playback sees it: an element, a page, and a lease on one.

`IBrowserAdapter` isolates Selenium (or CDP, or Playwright) from everything
above it. `IWorkflowElement` is the slice of a Selenium WebElement used here,
so a real WebElement satisfies it unchanged.
"""
from __future__ import annotations

from typing import Protocol

from browser_session.models import DebugEndpoint, TabInfo

from ..models import ElementSummary


class IWorkflowElement( Protocol ):
    @property
    def text( self ) -> str: ...

    @property
    def tag_name( self ) -> str: ...

    @property
    def rect( self ) -> dict[ str, float ]: ...

    def is_displayed( self ) -> bool: ...

    def is_enabled( self ) -> bool: ...

    def is_selected( self ) -> bool: ...

    def get_attribute( self, name: str ) -> str | None: ...

    def click( self ) -> None: ...

    def clear( self ) -> None: ...

    def send_keys( self, *value: str ) -> None: ...


class IElementQuery( Protocol ):
    """Lists the page's elements by CSS selector."""

    def find( self, css_selector: str ) -> list[ IWorkflowElement ]: ...


class IPageState( Protocol ):
    """What a wait condition can observe about the page."""

    @property
    def current_url( self ) -> str: ...

    @property
    def title( self ) -> str: ...

    def ready_state( self ) -> str: ...

    def body_text( self ) -> str: ...


class IPageNavigator( Protocol ):
    def navigate( self, url: str ) -> None: ...


class IFormControls( Protocol ):
    """The two inputs that are not a click or keys."""

    def select_option( self, element: IWorkflowElement, value: str ) -> None: ...

    def press_key( self, element: IWorkflowElement | None, key: str ) -> None: ...


class IPageInspector( Protocol ):
    """Evidence for diagnostics: a picture and the page's visible controls."""

    def screenshot( self, path: str ) -> bool: ...

    def interactive_elements( self, limit: int = 60 ) -> list[ ElementSummary ]: ...


class ITabs( Protocol ):
    def tabs( self ) -> list[ TabInfo ]: ...

    def focus( self, handle: str ) -> None: ...


class IDownloadSettings( Protocol ):
    def configure_downloads( self, directory: str ) -> None:
        """Save website downloads directly to a native path, without Save As."""
        ...


class IBrowserAdapter( IElementQuery, IPageState, IPageNavigator, IFormControls,
                       IPageInspector, ITabs, IDownloadSettings, Protocol ):
    """One attached browser, all its roles. A caller that needs one role asks for that one."""

    def release( self ) -> None:
        """Let go WITHOUT closing it -- it is the user's window."""
        ...


class IBrowserSessionProvider( Protocol ):
    """Factory: an attached browser, from a debug endpoint. Never launches one."""

    def attach( self, endpoint: DebugEndpoint ) -> IBrowserAdapter: ...


class IBrowserLease( Protocol ):
    """Holds the one browser a run is using, so every step uses the same one
    and the harness can always release it, whatever happened."""

    def acquire( self, endpoint: DebugEndpoint ) -> IBrowserAdapter: ...

    def current( self ) -> IBrowserAdapter: ...

    @property
    def held( self ) -> bool: ...

    def release( self ) -> None: ...


class IBrowserAttachment( Protocol ):
    """Opens a lease on the debug Chrome, asking first whether one is
    listening (chromedriver pointed at a dead port hangs for a minute)."""

    def open( self, endpoint: DebugEndpoint ) -> IBrowserLease: ...
