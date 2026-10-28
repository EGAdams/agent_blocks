"""Is a debuggable Chrome listening -- now, or after a short wait?"""
from __future__ import annotations

from typing import Any, Protocol

from ..models import DebugEndpoint, EndpointCheck


class IJsonFetcher( Protocol ):
    """GET a URL and parse its JSON body. Raises on any failure; callers
    decide what a failure means."""

    def get_json( self, url: str, timeout_seconds: float ) -> Any: ...


class IEndpointProbe( Protocol ):
    def check( self, endpoint: DebugEndpoint ) -> EndpointCheck: ...


class IReadinessWaiter( Protocol ):
    def wait( self, endpoint: DebugEndpoint ) -> EndpointCheck: ...
