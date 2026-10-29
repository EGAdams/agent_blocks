"""CdpBrowserSessionProvider: IBrowserSessionProvider over the DevTools protocol.

Attaching never launches anything: it lists the debug Chrome's tabs, lets
the injected ITargetChooser pick one, and opens a channel on it.
"""
from __future__ import annotations

from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError

from .browser import CdpWorkflowBrowser
from .channel import WebSocketCdpConnector
from .choosers import FirstPageChooser
from .interfaces import ICdpConnector, ITargetChooser, ITargetDirectory
from .targets import HttpTargetDirectory, LoopbackOnly


class CdpBrowserSessionProvider:
    def __init__( self, directory: ITargetDirectory, connector: ICdpConnector, chooser: ITargetChooser ) -> None:
        self._directory = directory
        self._connector = connector
        self._chooser   = chooser

    def attach( self, endpoint: DebugEndpoint ) -> CdpWorkflowBrowser:
        target = self._chooser.choose( self._directory.pages( endpoint ))
        if target is None:
            raise BrowserWorkflowError( f"the debug Chrome at { endpoint.address } has no open web page" )
        return CdpWorkflowBrowser( endpoint, target, self._directory, self._connector )


def default_cdp_provider( chooser: ITargetChooser | None = None ) -> CdpBrowserSessionProvider:
    """The loopback-guarded stock wiring. A composition root's helper, nothing more."""
    return CdpBrowserSessionProvider( HttpTargetDirectory( LoopbackOnly()), WebSocketCdpConnector(),
                                      chooser or FirstPageChooser())
