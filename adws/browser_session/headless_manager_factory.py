"""IBrowserManagerFactory over selenium-headless's WebBrowserManager.

WebBrowserManager normally launches its own Chrome; given a ready `driver` it
uses that instead, which is the whole trick of attaching. The only module in
this package that imports selenium-headless, which is a separate repository
with no packaging and so is reached by path (as in adw_interface_search_open.py).
"""
from __future__ import annotations

import sys

from .interfaces import IBrowserManager, IWindowDriver

SELENIUM_HEADLESS_ROOT = "/home/adamsl/selenium-headless"


class SeleniumHeadlessManagerFactory:

    def __init__( self, headless_root: str = SELENIUM_HEADLESS_ROOT ):
        self._headless_root = headless_root

    def wrap( self, driver: IWindowDriver ) -> IBrowserManager:
        if self._headless_root not in sys.path:
            sys.path.insert( 0, self._headless_root )
        from browser_manager import WebBrowserManager            # noqa: PLC0415  (see module docstring)

        return WebBrowserManager( driver=driver )
