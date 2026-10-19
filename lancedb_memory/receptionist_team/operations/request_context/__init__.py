"""ContextVar Adapter: request identity survives async calls without global turn state."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from ..models import ConversationRequest


class ScopedTurnContext:
    def __init__(self):
        self._request: ContextVar[ConversationRequest] = ContextVar('receptionist_request')

    @contextmanager
    def bind(self, request: ConversationRequest) -> Iterator[None]:
        token = self._request.set(request)
        try:
            yield
        finally:
            self._request.reset(token)

    def current(self) -> ConversationRequest:
        return self._request.get()
