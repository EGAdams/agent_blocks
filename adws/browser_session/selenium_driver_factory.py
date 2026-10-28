"""IDriverFactory over Selenium: a Chrome driver connected to an already
running Chrome through `debugger_address`, instead of launching one.

The only module in this package that imports Selenium, and only inside the
method, so `--help` and every test pay nothing for it.
"""
from __future__ import annotations

from .interfaces import IWindowDriver
from .models import DebugEndpoint


class SeleniumAttachedDriverFactory:

    def attached( self, endpoint: DebugEndpoint ) -> IWindowDriver:
        from selenium import webdriver                            # noqa: PLC0415  (see module docstring)
        from selenium.webdriver.chrome.options import Options     # noqa: PLC0415

        options = Options()
        options.debugger_address = endpoint.address
        return webdriver.Chrome( options=options )
