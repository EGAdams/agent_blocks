"""IDetachPolicy: how to let go of the user's browser.

Deliberately NOT WebBrowserManager.quit(): that ends the browser, and this
browser is the user's, mid-login. Stopping chromedriver detaches and leaves
every window exactly as it was.
"""
from __future__ import annotations

from .interfaces import IWindowDriver


class StopDriverServiceDetach:
    def detach( self, driver: IWindowDriver ) -> None:
        try:
            driver.service.stop()
        except Exception:
            pass
