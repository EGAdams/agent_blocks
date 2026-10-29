"""PinnedTabConnection: IBrowserConnection -- attach to the session's tab.

It remembers the last tab it was asked for, and `preference` hands that
to the PreferredTargetChooser the playback kit uses, so a learned playback
runs in the same tab the conversation is in.
"""
from __future__ import annotations

from browser_session.models import DebugEndpoint
from browser_workflows.interfaces.browser import IBrowserSessionProvider

from .interfaces.browser import ICashyBrowser


class PinnedTabConnection:
    def __init__( self, endpoint: DebugEndpoint ) -> None:
        self._endpoint = endpoint
        self._provider: IBrowserSessionProvider | None = None
        self._wanted: tuple[ str, list[ str ]] = ( "", [])

    def use( self, provider: IBrowserSessionProvider ) -> None:
        self._provider = provider

    def preference( self ) -> tuple[ str, list[ str ]]:
        return self._wanted

    def attach( self, target_id: str, hosts: list[ str ]) -> ICashyBrowser:
        self._wanted = ( target_id, list( hosts ))
        browser = self._provider.attach( self._endpoint )
        self._wanted = ( browser.target_id, list( hosts ))
        return browser
