"""BridgeConfirmationRoute -- a bare "yes" or "no" goes to the Bridge specialist waiting for it.

A Bridge specialist that would change a file, print, cancel a print or scan
reads the request back and waits (bridge_client/desk.PENDING). The answer is
one short word, which Toyota's model can't be trusted to route: "yes" has no
subject. So while a request is waiting, a reply of six words or fewer that
reads as yes or no is sent straight to the specialist that asked. With
nothing waiting, this route has no opinion and the model decides as before.

It goes first in Toyota's FirstRouteOpinion, ahead of every keyword route.
"""
from .latest_user import latest_user_message


class BridgeConfirmationRoute:
    def __init__(self, pending: object | None = None) -> None:
        if pending is None:
            from ..bridge_client.desk import PENDING
            pending = PENDING
        self._pending = pending

    def forced_agent(self, prompt: str) -> str | None:
        from ..bridge_client.desk import yes_or_no
        waiting = self._pending.peek()
        if waiting is None or yes_or_no(latest_user_message(prompt)) is None:
            return None
        return waiting.specialist

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
