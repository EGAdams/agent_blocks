"""IReadinessWaiter: ask the probe until it says yes or time runs out."""
from __future__ import annotations

from .interfaces import IClock, IEndpointProbe
from .models import DebugEndpoint, EndpointCheck


class PollingReadinessWaiter:

    def __init__( self, probe: IEndpointProbe, clock: IClock,
                  timeout_seconds: float = 15.0, interval_seconds: float = 0.5 ):
        self._probe    = probe
        self._clock    = clock
        self._timeout  = timeout_seconds
        self._interval = interval_seconds

    def wait( self, endpoint: DebugEndpoint ) -> EndpointCheck:
        deadline = self._clock.now() + self._timeout
        check = self._probe.check( endpoint )
        while not check.reachable and self._clock.now() < deadline:
            self._clock.sleep( self._interval )
            check = self._probe.check( endpoint )
        return check
