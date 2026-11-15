"""IAlerter: the same alert on every machine's desktop -- here by toast, on each peer by an `alert` handoff."""
from __future__ import annotations

from agent_comms.models import TaskStatus

from ..errors import Undelivered
from ..interfaces import IMachineCatalog, IMessenger, IToast
from ..models import Alert

PRIORITY = 10
TIMEOUT = 45


class AllMachinesAlerter:
    def __init__(self, catalog: IMachineCatalog, toast: IToast, messenger: IMessenger) -> None:
        self._catalog, self._toast, self._messenger = catalog, toast, messenger

    def alert(self, alert: Alert) -> list[str]:
        me = self._catalog.me()
        try:
            lines = [f"{me.name}: {self._toast.show(alert)}"]
        except RuntimeError as error:
            lines = [f"{me.name}: NOT shown ({error})"]
        for peer in self._catalog.peers():
            try:
                reply = self._messenger.send(peer, "alert", alert.message, alert.model_dump(), TIMEOUT, PRIORITY)
                shown = reply.outcome is TaskStatus.SUCCEEDED
                lines.append(f"{peer.name}: {reply.result if shown else 'NOT shown (' + (reply.error or '?') + ')'}")
            except Undelivered as error:
                lines.append(f"{peer.name}: NOT shown, machine not reached ({error})")
        return lines
