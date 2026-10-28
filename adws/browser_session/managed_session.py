"""IBrowserSession (Adapter) over an IBrowserManager this run attached to
but does not own. Element lookups go through the manager's own
`find_elements`; tab work through its driver."""
from __future__ import annotations

from .interfaces import IBrowserManager, IDetachPolicy, IPageElement
from .models import TabInfo


class ManagedBrowserSession:

    def __init__( self, manager: IBrowserManager, detach: IDetachPolicy ):
        self._manager = manager
        self._detach  = detach

    def tabs( self ) -> list[ TabInfo ]:
        driver  = self._manager.driver
        current = driver.current_window_handle
        tabs = []
        for handle in driver.window_handles:
            driver.switch_to.window( handle )
            tabs.append( TabInfo( handle=handle, url=driver.current_url ))
        driver.switch_to.window( current )
        return tabs

    def focus( self, handle: str ) -> None:
        self._manager.driver.switch_to.window( handle )

    def find( self, css_selector: str ) -> list[ IPageElement ]:
        return list( self._manager.find_elements( css_selector ))

    def release( self ) -> None:
        self._detach.detach( self._manager.driver )
