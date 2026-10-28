"""ISessionLogFactory over sssf_roles' ConsoleLifecycleLog."""
from __future__ import annotations

from sssf_roles import ConsoleLifecycleLog, ILifecycleLog

from adw_modules.runner import Run


class ConsoleSessionLogFactory:
    def create( self, run: Run ) -> ILifecycleLog:
        return ConsoleLifecycleLog( run )
