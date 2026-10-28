"""The page as this package sees it: an element, and a session holding tabs."""
from __future__ import annotations

from typing import Protocol

from ..models import TabInfo


class IPageElement( Protocol ):
    """The slice of a Selenium WebElement used here. A real WebElement
    satisfies it unchanged, so nothing has to wrap one."""

    @property
    def text( self ) -> str: ...
    def is_displayed( self ) -> bool: ...
    def is_enabled( self ) -> bool: ...
    def click( self ) -> None: ...


class IElementFinder( Protocol ):
    """Anything that can list a page's elements by CSS selector."""

    def find( self, css_selector: str ) -> list[ IPageElement ]: ...


class ITabNavigator( Protocol ):
    """Lists the open tabs and moves between them."""

    def tabs( self ) -> list[ TabInfo ]: ...
    def focus( self, handle: str ) -> None: ...


class IReleasable( Protocol ):
    def release( self ) -> None:
        """Let go of the browser WITHOUT closing it -- it is the user's window."""
        ...


class IBrowserSession( IElementFinder, ITabNavigator, IReleasable, Protocol ):
    """A browser this run is attached to but does not own: the three roles
    above, combined. Callers that need one role ask for that one."""
