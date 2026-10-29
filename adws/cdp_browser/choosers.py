"""ITargetChooser: which tab to drive."""
from __future__ import annotations

from collections.abc import Callable

from .interfaces import CdpTarget


class FirstPageChooser:
    """Chrome lists the most recently focused tab first."""

    def choose( self, pages: list[ CdpTarget ]) -> CdpTarget | None:
        return pages[ 0 ] if pages else None


class PreferredTargetChooser:
    """The tab a session was using, else the first whose URL is on one of its
    hosts, else the fallback's pick. `preference` is read at choose time, so a
    session that moves to another tab is followed."""

    def __init__( self, preference: Callable[[], tuple[ str, list[ str ]]],
                  fallback: FirstPageChooser | None = None ) -> None:
        self._preference = preference
        self._fallback   = fallback or FirstPageChooser()

    def choose( self, pages: list[ CdpTarget ]) -> CdpTarget | None:
        target_id, hosts = self._preference()
        for page in pages:
            if target_id and page.id == target_id:
                return page
        for page in pages:
            if any( host and host in page.url for host in hosts ):
                return page
        return self._fallback.choose( pages )
