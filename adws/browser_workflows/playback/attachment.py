"""IBrowserAttachment: probe, then lease. Refuses in one line when no
debuggable Chrome is listening, naming the ADW that starts one."""
from __future__ import annotations

from typing import Callable

from browser_session.interfaces import IEndpointProbe
from browser_session.models import DebugEndpoint

from ..errors import BrowserWorkflowError
from ..interfaces.browser import IBrowserLease

START_HINT = "start one with: uv run adws/adw_interface_browser_debug_open.py \"<why>\" --url <page>"


class ProbedBrowserAttachment:
    def __init__( self, probe: IEndpointProbe, leases: Callable[[], IBrowserLease ]) -> None:
        self._probe  = probe
        self._leases = leases

    def open( self, endpoint: DebugEndpoint ) -> IBrowserLease:
        check = self._probe.check( endpoint )
        if not check.reachable:
            raise BrowserWorkflowError( f"no debuggable Chrome at { endpoint.address }: { check.reason }; { START_HINT }" )
        lease = self._leases()
        lease.acquire( endpoint )
        return lease
