"""IBrowserAttacher: driver factory -> manager factory -> session.
Knows the order of assembly and nothing about Selenium or selenium-headless."""
from __future__ import annotations

from .interfaces import IBrowserManagerFactory, IBrowserSession, IDetachPolicy, IDriverFactory
from .managed_session import ManagedBrowserSession
from .models import DebugEndpoint


class ManagerSessionAttacher:

    def __init__( self, drivers: IDriverFactory, managers: IBrowserManagerFactory, detach: IDetachPolicy ):
        self._drivers  = drivers
        self._managers = managers
        self._detach   = detach

    def attach( self, endpoint: DebugEndpoint ) -> IBrowserSession:
        return ManagedBrowserSession( self._managers.wrap( self._drivers.attached( endpoint )), self._detach )
