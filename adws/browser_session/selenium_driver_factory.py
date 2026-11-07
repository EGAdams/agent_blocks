"""IDriverFactory over Selenium: a Chrome driver connected to an already
running Chrome through `debugger_address`, instead of launching one.

The only module in this package that imports Selenium, and only inside the
method, so `--help` and every test pay nothing for it.

The Chrome here runs on Windows and chromedriver on Linux, so Selenium
Manager cannot see the browser to pick a matching driver; left alone it
reuses whatever driver is cached, and a stale one refuses to attach
("This version of ChromeDriver only supports Chrome version 151"). So the
running Chrome's major version is read off its own debug port first and
asked for by name, and Selenium Manager fetches that driver.
"""
from __future__ import annotations

from .interfaces import IJsonFetcher, IWindowDriver
from .json_fetcher import UrllibJsonFetcher
from .models import DebugEndpoint


class SeleniumAttachedDriverFactory:

    def __init__( self, fetcher: IJsonFetcher | None = None, timeout_seconds: float = 2.0 ):
        self._fetcher = fetcher or UrllibJsonFetcher()
        self._timeout = timeout_seconds

    def major_version( self, endpoint: DebugEndpoint ) -> str:
        """'156' from 'Chrome/156.0.8078.80', or '' if the port won't say."""
        try:
            browser = str(( self._fetcher.get_json( f"http://{ endpoint.address }/json/version",
                                                     self._timeout ) or {}).get( "Browser", "" ))
        except Exception:
            return ""
        major = browser.partition( "/" )[ 2 ].split( "." )[ 0 ]
        return major if major.isdigit() else ""

    def attached( self, endpoint: DebugEndpoint ) -> IWindowDriver:
        from selenium import webdriver                            # noqa: PLC0415  (see module docstring)
        from selenium.webdriver.chrome.options import Options     # noqa: PLC0415

        options = Options()
        options.debugger_address = endpoint.address
        major = self.major_version( endpoint )
        if major:
            options.browser_version = major
        return webdriver.Chrome( options=options )
