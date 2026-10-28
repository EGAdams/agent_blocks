"""IEndpointProbe: GET /json/version on Chrome's DevTools address. Never raises.

Asked before Selenium starts, because chromedriver pointed at a dead address
hangs for a minute before failing; this answer is instant.
"""
from __future__ import annotations

from .interfaces import IJsonFetcher
from .models import DebugEndpoint, EndpointCheck


class DevToolsEndpointProbe:

    def __init__( self, fetcher: IJsonFetcher, timeout_seconds: float = 2.0 ):
        self._fetcher = fetcher
        self._timeout = timeout_seconds

    def check( self, endpoint: DebugEndpoint ) -> EndpointCheck:
        try:
            info = self._fetcher.get_json( f"http://{ endpoint.address }/json/version", self._timeout )
        except Exception as e:
            return EndpointCheck( reachable=False, reason=f"nothing answered at { endpoint.address } ({ e })" )
        return EndpointCheck( reachable=True, browser=str(( info or {}).get( "Browser", "" )))
