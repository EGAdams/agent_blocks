"""IBrowserLease: the one browser a run uses, held so it can always be let go."""
from __future__ import annotations

from browser_session.models import DebugEndpoint

from ..errors import BrowserWorkflowError
from ..interfaces.browser import IBrowserAdapter, IBrowserSessionProvider


class BrowserLease:
    def __init__( self, provider: IBrowserSessionProvider ) -> None:
        self._provider = provider
        self._browser: IBrowserAdapter | None = None

    def acquire( self, endpoint: DebugEndpoint ) -> IBrowserAdapter:
        if self._browser is None:
            self._browser = self._provider.attach( endpoint )
        return self._browser

    def current( self ) -> IBrowserAdapter:
        if self._browser is None:
            raise BrowserWorkflowError( "no browser attached yet" )
        return self._browser

    @property
    def held( self ) -> bool:
        return self._browser is not None

    def release( self ) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            browser.release()


class FixedBrowserLease:
    """A lease on a browser someone else attached (tests, a caller holding one)."""

    def __init__( self, browser: IBrowserAdapter ) -> None:
        self._browser = browser

    def acquire( self, endpoint: DebugEndpoint ) -> IBrowserAdapter:
        return self._browser

    def current( self ) -> IBrowserAdapter:
        return self._browser

    @property
    def held( self ) -> bool:
        return True

    def release( self ) -> None:
        pass
